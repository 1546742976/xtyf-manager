"""Personal morning and team evening briefs with a durable SMTP queue."""
import hashlib
import hmac
from html import escape as html_escape
import os
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from urllib.parse import urlsplit

from flask import current_app
from modules.daily_projects import TZ, describe_many
from modules.mail_templates import render_digest_html, render_verification_html, render_test_html
from modules.task_assignment_mail import assignment_content


def settings():
    return current_app.config['MAIL_SETTINGS']


def configure(app):
    app.config['MAIL_SETTINGS'] = {
        'mode': os.environ.get('MAIL_MODE', 'off'),
        'host': os.environ.get('SMTP_HOST', ''),
        'port': int(os.environ.get('SMTP_PORT', '465')),
        'tls': os.environ.get('SMTP_TLS', 'ssl'),
        'username': os.environ.get('SMTP_USERNAME', ''),
        'password': os.environ.get('SMTP_PASSWORD', ''),
        'sender': os.environ.get('SMTP_FROM', ''),
        'base_url': os.environ.get('PUBLIC_BASE_URL', '').rstrip('/'),
        'preview_dir': os.environ.get('MAIL_PREVIEW_DIR', str(Path(app.config['TASK_ATTACHMENTS_DIR']).parent / 'mail-preview')),
    }


def configured():
    cfg = settings()
    if cfg['mode'] not in ('smtp', 'preview'):
        return False
    url = urlsplit(cfg['base_url'])
    if not url.netloc or url.username or url.password or url.query or url.fragment:
        return False
    if cfg['mode'] == 'preview':
        return url.scheme in ('http', 'https')
    return bool(url.scheme == 'https' and cfg['host'] and cfg['sender'] and cfg['tls'] in ('ssl', 'starttls'))


def verification_code(generation):
    """Derive a six-digit one-time code without storing a recoverable code in SQLite."""
    digest = hmac.new(current_app.secret_key.encode(), ('email-code:' + generation).encode(), hashlib.sha256).digest()
    return f'{int.from_bytes(digest[:8], "big") % 1_000_000:06d}'


def verification_digest(generation, code):
    """Keyed verifier: a database dump alone must not allow guessing 1e6 possible codes."""
    message = f'email-code-check:{generation}:{code}'.encode()
    return hmac.new(current_app.secret_key.encode(), message, hashlib.sha256).hexdigest()


def queue(db, mid, kind, day, generation, at, key):
    db.execute('INSERT OR IGNORE INTO mail_jobs(member_id,kind,day,dedupe_key,generation,next_at,created_at) VALUES(?,?,?,?,?,?,?)',
               (mid, kind, day, key, generation, at, at))


def parsed_time(value):
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def beijing_time(at):
    return at.replace(tzinfo=TZ) if at.tzinfo is None else at.astimezone(TZ)


def daily_window(at, day=None, kind='daily'):
    at = beijing_time(at)
    windows = {'morning': (8, 12), 'morning_pm': (20, 22),
               'daily': (22, 24), 'countdown': (8, 12)}
    start, end = windows.get(kind, windows['daily'])
    return start <= at.hour < end and (day is None or day == at.date().isoformat())


def cadence_due(db, member_id, kind, interval_hours, at, countdown_version=None):
    """Measure from last accepted/uncertain delivery, not from empty skipped reports."""
    kinds = ('morning', 'morning_pm') if kind in ('morning', 'morning_pm') else (kind,)
    placeholders = ','.join('?' for _ in kinds)
    params = [member_id, *kinds]
    version_clause = ''
    if kind == 'countdown':
        version_clause = " AND dedupe_key LIKE ?"
        params.append(f'countdown:v{countdown_version}:%')
    last = db.execute(
        f"SELECT max(COALESCE(finished_at,created_at)) FROM mail_jobs "
        f"WHERE member_id=? AND kind IN ({placeholders}) "
        f"AND state IN ('sent','simulated','unknown')" + version_clause, params).fetchone()[0]
    return last is None or at.timestamp() - last >= interval_hours * 3600



def stale(last, progress, at):
    return progress < 100 and (beijing_time(at) - parsed_time(last)).total_seconds() > 48 * 3600


def deadline(due, at):
    if not due:
        return '未设置截止日期'
    days = (datetime.strptime(due, '%Y-%m-%d').date() - beijing_time(at).date()).days
    return f'已逾期 {-days} 天' if days < 0 else '今天截止' if days == 0 else f'剩余 {days} 天'


def digest_items(db, mid, at, kind='daily'):
    at = beijing_time(at)
    morning = kind == 'morning'
    today = at.date().isoformat()
    helper_names = {row['id']: row['name'] for row in db.execute('SELECT id,name FROM members')}
    def blocker(row):
        item = dict(row)
        return {key: item.get(key) for key in ('blocker_reason', 'blocker_next_action', 'blocker_follow_up')} | {
            'blocker_helper_name': helper_names.get(item.get('blocker_helper_id'), '')}
    tasks = []
    task_scope = (" AND (t.owner_id=? OR EXISTS(SELECT 1 FROM task_participants tp "
                  "WHERE tp.task_id=t.id AND tp.member_id=?))" if morning else "")
    for row in db.execute("SELECT t.*, (SELECT max(created_at) FROM task_progress_updates WHERE task_id=t.id) AS last_progress "
                          "FROM tasks t WHERE t.deleted_at IS NULL "
                          "AND (t.status IN ('进行中','阻塞') OR "
                          "(t.status='未开始' AND t.due_date<>'' AND t.due_date<?))"
                          + task_scope + " "
                          "ORDER BY CASE WHEN t.due_date IS NULL OR t.due_date='' THEN 1 ELSE 0 END,t.due_date,t.id",
                          (today, mid, mid) if morning else (today,)):
        owner = row['owner_id'] == mid
        last = row['last_progress'] or row['created_at']
        task = {'id': row['id'], 'title': row['title'], 'status': row['status'],
                'progress': row['progress'], 'due': row['due_date'], 'remaining': deadline(row['due_date'], at),
                'nudge': owner and row['status'] != '已完成' and stale(last, row['progress'], at),
                'url': settings()['base_url'] + f'/tasks?task={row["id"]}'}
        if morning:
            task.update(role='负责人' if owner else '参与者', last_progress=row['last_progress'],
                        reminder_baseline=last)
        task.update(blocker(row))
        tasks.append(task)
    projects = []
    project_scope = (" AND EXISTS(SELECT 1 FROM daily_owners o JOIN daily_groups g "
                     "USING(project_id,stage) WHERE o.project_id=p.id AND o.member_id=? AND g.enabled=1)"
                     if morning else "")
    rows = db.execute("SELECT p.* FROM daily_projects p WHERE p.deleted_at IS NULL "
                      "AND (p.status IN ('进行中','阻塞') OR "
                      "(p.status='未开始' AND p.due_date<>'' AND p.due_date<?))"
                      + project_scope + " "
                      "ORDER BY CASE WHEN p.due_date IS NULL OR p.due_date='' THEN 1 ELSE 0 END,p.due_date,p.id",
                      (today, mid) if morning else (today,))
    for project in describe_many(db, rows):
        row = project
        groups = []
        for group in project['groups']:
            if not group['enabled']:
                continue
            last = group['latest']['created_at'] if group['latest'] else group['enabled_at']
            # Re-enabling a previously inactive group starts a new reminder interval.
            baseline = max(last, group['enabled_at'], key=parsed_time)
            responsible = any(owner['id'] == mid and owner['is_active'] and not owner['deleted_at']
                              for owner in group['owners'])
            if morning and not responsible:
                continue
            item = {'label': group['label'], 'progress': group['progress'],
                    'nudge': responsible and row['status'] != '已完成' and stale(baseline, group['progress'], at)}
            if morning:
                item.update(last_progress=group['latest']['created_at'] if group['latest'] else None,
                            reminder_baseline=baseline)
            groups.append(item)
        if morning and not groups:
            continue
        projects.append({'id': row['id'], 'title': row['title'], 'status': row['status'],
                         'progress': project['progress'], 'groups': groups,
                         'due': row['due_date'], 'remaining': deadline(row['due_date'], at),
                         'url': settings()['base_url'] + f'/tasks?type=daily&project={row["id"]}', **blocker(row)})
    return tasks, projects


def digest_content(db, mid, at, kind='daily'):
    tasks, projects = digest_items(db, mid, at, kind=kind)
    if not tasks and not projects:
        return None
    morning = kind == 'morning'
    title = '个人任务早报' if morning else '团队项目晚报'
    local_time = beijing_time(at)
    nudges = sum(bool(item['nudge']) for item in tasks) + sum(
        bool(group['nudge']) for project in projects for group in project['groups'])
    overdue = sum(item['remaining'].startswith('已逾期') for item in tasks + projects)
    summary = f'需更新进度 {nudges} 处' if morning else f'已逾期 {overdue} 项'
    lines = [title, local_time.strftime('%Y-%m-%d'),
             f'造车任务 {len(tasks)} 项｜日常项目 {len(projects)} 项｜{summary}', '', '造车任务']

    def progress_time(item, baseline_label):
        if item['last_progress']:
            return '最近进度更新：' + parsed_time(item['last_progress']).strftime('%Y-%m-%d %H:%M:%S')
        return '暂无进度更新（' + baseline_label + parsed_time(item['reminder_baseline']).strftime('%Y-%m-%d %H:%M:%S') + '）'

    def blocker_text(item):
        if item['status'] != '阻塞': return ''
        text = '\n阻塞原因：' + (item.get('blocker_reason') or '待补充')
        text += '\n下一步行动：' + (item.get('blocker_next_action') or '待补充')
        if item.get('blocker_helper_name'): text += '\n协助成员：' + item['blocker_helper_name']
        if item.get('blocker_follow_up'): text += '\n下次跟进：' + item['blocker_follow_up']
        return text

    if not tasks:
        lines.append('暂无您负责或参与且符合提醒条件的造车任务' if morning else '暂无团队符合提醒条件的造车任务')
    for task in tasks:
        text = f'{task["title"]}｜{task["status"]}｜{task["progress"]}%｜{task["remaining"]}'
        if task['due']:
            text += f'（{task["due"]}）'
        if morning:
            text += f'\n您的角色：{task["role"]}\n' + progress_time(task, '创建于 ')
        if task['nudge']:
            text += '\n请更新进度：您负责的任务已超过 48 小时未更新。'
        lines.extend([text + blocker_text(task), task['url'], ''])
    lines.extend(['', '日常项目'])
    if not projects:
        lines.append('暂无您负责且符合提醒条件的日常项目' if morning else '暂无团队符合提醒条件的日常项目')
    for project in projects:
        text = f'{project["title"]}｜{project["status"]}｜{project["progress"]:g}%｜{project["remaining"]}'
        if project['due']:
            text += f'（{project["due"]}）'
        text += '\n' + ('您负责的小组：' if morning else '') + ' · '.join(
            f'{group["label"]} {group["progress"]}%' for group in project['groups'])
        for group in project['groups']:
            if morning:
                text += f'\n{group["label"]}组' + progress_time(group, '启用于 ')
            if group['nudge']:
                text += f'\n请更新进度：您负责的{group["label"]}组已超过 48 小时未更新。'
        lines.extend([text + blocker_text(project), project['url'], ''])
    footer = ('截至发送时您负责或参与且符合提醒条件的事项；按设定周期在北京时间 08:00（12 小时间隔时还包括 20:00）汇总，包含周末。'
              if morning else '截至发送时全团队符合提醒条件的事项；按设定周期在北京时间 22:00 汇总，包含周末。')
    footer += ('仅包含进行中、阻塞，以及未开始但已逾期的事项。逾期指截止日期早于发送当天（北京时间），'
               '今天截止不算逾期。已完成（包括当天完成）、已取消和已删除事项始终排除。时间均为北京时间。')
    footer += '仅对您负责且超过 48 小时未更新的事项提示催更。如需暂停，请联系管理员。'
    lines.append(footer)
    markup = render_digest_html(title, local_time, tasks, projects, morning, footer, progress_time)
    return '\n'.join(lines), markup


def active_countdown(db, at):
    row = db.execute('SELECT title,target_date,version FROM workspace_countdown WHERE id=1').fetchone()
    if not row or not row['title'] or not row['target_date']:
        return None
    days = (datetime.strptime(row['target_date'], '%Y-%m-%d').date() - beijing_time(at).date()).days
    return (row, days) if days > 0 else None


def enqueue_daily(db, at):
    at = beijing_time(at)
    kinds = tuple(kind for kind in ('morning', 'morning_pm', 'daily', 'countdown')
                  if daily_window(at, kind=kind))
    if not kinds:
        return
    day, epoch = at.date().isoformat(), at.timestamp()
    db.execute('BEGIN IMMEDIATE')
    try:
        countdown = active_countdown(db, at) if 'countdown' in kinds else None
        for row in db.execute('SELECT e.* FROM member_email e JOIN members m ON m.id=e.member_id '
                              'WHERE e.verified=1 AND e.paused=0 AND m.is_active=1 AND m.deleted_at IS NULL'):
            mid = row['member_id']
            if 'morning' in kinds and row['morning_enabled'] and cadence_due(
                    db, mid, 'morning', row['morning_interval_hours'], at):
                queue(db, mid, 'morning', day, row['generation'], epoch, f'morning:{day}:{mid}')
            if 'morning_pm' in kinds and row['morning_enabled'] and row['morning_interval_hours'] == 12 and cadence_due(
                    db, mid, 'morning_pm', 12, at):
                queue(db, mid, 'morning_pm', day, row['generation'], epoch, f'morning_pm:{day}:{mid}')
            if 'daily' in kinds and row['daily_enabled'] and cadence_due(
                    db, mid, 'daily', row['daily_interval_days'] * 24, at):
                queue(db, mid, 'daily', day, row['generation'], epoch, f'daily:{day}:{mid}')
            if countdown and row['countdown_enabled'] and cadence_due(
                    db, mid, 'countdown', row['countdown_interval_days'] * 24, at, countdown[0]['version']):
                queue(db, mid, 'countdown', day, row['generation'], epoch,
                      f'countdown:v{countdown[0]["version"]}:{day}:{mid}')
        db.commit()
    except Exception:
        db.rollback(); raise


class DeliveryError(Exception):
    def __init__(self, reason, state='failed', retry=False):
        super().__init__(reason)
        self.state, self.retry = state, retry


def smtp_send(message):
    cfg, client, submitted = settings(), None, False
    try:
        context = ssl.create_default_context()
        if cfg['tls'] == 'ssl':
            client = smtplib.SMTP_SSL(cfg['host'], cfg['port'], timeout=30, context=context)
        else:
            client = smtplib.SMTP(cfg['host'], cfg['port'], timeout=30)
            client.ehlo(); client.starttls(context=context); client.ehlo()
        if cfg['username']:
            client.login(cfg['username'], cfg['password'])
        code, _ = client.mail(message['From'])
        if code != 250:
            raise DeliveryError(f'SMTP 发件人被拒绝（{code}）', retry=400 <= code < 500)
        code, _ = client.rcpt(message['To'])
        if code not in (250, 251):
            raise DeliveryError(f'SMTP 收件地址被拒绝（{code}）', retry=400 <= code < 500)
        submitted = True
        code, _ = client.data(message.as_bytes(policy=SMTP))
        if code != 250:
            raise DeliveryError(f'SMTP 拒绝邮件（{code}）', retry=400 <= code < 500)
    except DeliveryError:
        raise
    except smtplib.SMTPAuthenticationError:
        raise DeliveryError('SMTP 认证失败，请检查服务器配置') from None
    except smtplib.SMTPResponseException as exc:
        raise DeliveryError(f'SMTP 拒绝请求（{exc.smtp_code}）', retry=400 <= exc.smtp_code < 500) from None
    except ssl.SSLError:
        raise DeliveryError('SMTP TLS 校验失败，请检查服务器配置') from None
    except (OSError, smtplib.SMTPException):
        raise DeliveryError('SMTP 提交后连接中断，结果未知' if submitted else 'SMTP 连接失败',
                            state='unknown' if submitted else 'failed', retry=not submitted) from None
    finally:
        if client:
            # A QUIT failure must not turn an accepted message into a retry.
            try:
                client.close()
            except OSError:
                pass


def claim(db, at):
    at = beijing_time(at)
    epoch = at.timestamp()
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute("UPDATE mail_jobs SET state='unknown',error='发送进程中断，结果未知',finished_at=? WHERE state='sending' AND claimed_at<?", (epoch, epoch-1800))
        for kind, label, start, end in (('morning', '早报', 8, 12), ('morning_pm', '晚间个人简报', 20, 22),
                                        ('daily', '晚报', 22, 24), ('countdown', '倒计时', 8, 12)):
            db.execute("UPDATE mail_jobs SET state='skipped',error=?,finished_at=? WHERE kind=? AND state='pending' "
                       "AND (day<>? OR ? < ? OR ? >= ?)",
                       (f'已超出当日{label}窗口', epoch, kind, at.date().isoformat(), at.hour, start, at.hour, end))
        row = db.execute("SELECT * FROM mail_jobs WHERE state='pending' AND next_at<=? ORDER BY CASE kind WHEN 'verify' THEN 0 WHEN 'test' THEN 1 WHEN 'assignment' THEN 2 ELSE 3 END,id LIMIT 1", (epoch,)).fetchone()
        if row:
            db.execute("UPDATE mail_jobs SET state='sending',claimed_at=?,attempts=attempts+1 WHERE id=?", (epoch, row['id']))
        db.commit()
        return dict(row) | {'attempts': row['attempts']+1} if row else None
    except Exception:
        db.rollback(); raise


def process_job(db, job, at, transport=None):
    at = beijing_time(at)
    epoch, state, error, next_at = at.timestamp(), 'sent', '', at.timestamp()
    try:
        if job['kind'] in ('morning', 'morning_pm', 'daily', 'countdown') and not daily_window(
                at, job['day'], kind=job['kind']):
            raise DeliveryError('已超出当天发送窗口', 'skipped')
        member = db.execute('SELECT e.*,m.is_active,m.deleted_at FROM member_email e JOIN members m ON m.id=e.member_id WHERE e.member_id=?', (job['member_id'],)).fetchone()
        if not member or not member['is_active'] or member['deleted_at'] or member['generation'] != job['generation']:
            raise DeliveryError('账户或邮箱已变化', 'skipped')
        message = EmailMessage()
        message['From'] = settings()['sender'] or 'preview@example.invalid'
        message['To'] = member['address']
        message['Message-ID'] = f'<xtyf-{job["id"]}-{job["generation"]}@{message["From"].split("@")[-1]}>'
        if job['kind'] == 'verify':
            if member['verified'] or not member['token_hash'] or member['verify_attempts'] >= 5 or member['expires_at'] <= epoch:
                raise DeliveryError('验证已完成或过期', 'skipped')
            code = verification_code(member['generation'])
            message['Subject'] = '巡天御风｜邮箱验证码'
            text = f'您的邮箱验证码是：{code}\n请在网站的个人账户页面输入验证码，30 分钟内有效。请勿转发给他人。\n验证后可在个人账户中设置个人、团队及倒计时邮件的提醒频率；无符合提醒条件的事项时将跳过汇总邮件。如非本人操作请忽略。'
            markup = render_verification_html(code)
        else:
            if not member['verified'] or member['paused']:
                raise DeliveryError('邮箱未验证或提醒已暂停', 'skipped')
            periodic = job['kind'] in ('morning', 'morning_pm', 'daily', 'countdown')
            countdown = active_countdown(db, at) if job['kind'] == 'countdown' else None
            if periodic:
                field = 'morning_enabled' if job['kind'].startswith('morning') else (
                    'daily_enabled' if job['kind'] == 'daily' else 'countdown_enabled')
                if not member[field]:
                    raise DeliveryError('该类邮件已关闭', 'skipped')
                if job['kind'] == 'morning_pm' and member['morning_interval_hours'] != 12:
                    raise DeliveryError('个人简报间隔已更改', 'skipped')
                if job['kind'] == 'countdown' and (not countdown or not job['dedupe_key'].startswith(
                        f'countdown:v{countdown[0]["version"]}:')):
                    raise DeliveryError('倒计时目标已变化或结束', 'skipped')
                hours = (member['morning_interval_hours'] if job['kind'].startswith('morning')
                         else member['daily_interval_days'] * 24 if job['kind'] == 'daily'
                         else member['countdown_interval_days'] * 24)
                if not cadence_due(db, job['member_id'], job['kind'], hours, at,
                                   countdown[0]['version'] if countdown else None):
                    raise DeliveryError('尚未达到成员设定的发送间隔', 'skipped')
            if job['kind'] == 'assignment':
                content, skip_reason = assignment_content(db, job, member, epoch, settings()['base_url'])
                if skip_reason:
                    raise DeliveryError(skip_reason, 'skipped')
                text, markup = content
                message['Subject'] = '巡天御风｜任务指派'
            elif job['kind'] == 'countdown':
                target, days = countdown
                title = html_escape(target['title'])
                url = html_escape(settings()['base_url'] + '/', quote=True)
                text = (f'首页目标倒计时：{target["title"]}\n'
                        f'目标日期：{target["target_date"]}\n'
                        f'距离目标还有 {days} 天\n{settings()["base_url"]}/')
                markup = (f'<html><body><h2>首页目标倒计时</h2><p>{title}</p>'
                          f'<p>目标日期：{target["target_date"]} · 剩余 {days} 天</p>'
                          f'<p><a href="{url}">查看工作台</a></p></body></html>')
                message['Subject'] = '巡天御风｜目标倒计时｜' + job['day']
            else:
                digest_kind = 'morning' if job['kind'].startswith('morning') else job['kind']
                content = digest_content(db, job['member_id'], at, kind=digest_kind)
                if not content and job['kind'] in ('morning', 'morning_pm', 'daily'):
                    raise DeliveryError('个人范围没有符合提醒条件的事项' if job['kind'].startswith('morning') else '全团队没有符合提醒条件的事项', 'skipped')
                text, markup = content or ('测试邮件：当前没有团队符合提醒条件的事项。', render_test_html())
                subjects = {'morning': '巡天御风｜个人任务早报｜', 'daily': '巡天御风｜团队项目晚报｜'}
                message['Subject'] = subjects.get(digest_kind, '巡天御风｜测试邮件｜') + job['day']
        message.set_content(text)
        message.add_alternative(markup, subtype='html')
        if transport:
            transport(message)
        elif settings()['mode'] == 'preview':
            root = Path(settings()['preview_dir']); root.mkdir(parents=True, exist_ok=True)
            if os.name == 'posix':
                root.chmod(0o700)
            fd = os.open(root / f'{job["id"]}.eml', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'wb') as file:
                file.write(message.as_bytes())
            state = 'simulated'
        else:
            smtp_send(message)
    except DeliveryError as exc:
        state, error = exc.state, str(exc)
        if (exc.retry and exc.state == 'failed' and job['attempts'] < 3
                and (job['kind'] not in ('morning', 'morning_pm', 'daily', 'countdown')
                     or daily_window(at + timedelta(minutes=15), job['day'], kind=job['kind']))):
            state, next_at = 'pending', epoch + (60 if job['kind'] in ('verify', 'test') else 900)
    except Exception:
        # Do not log exceptions containing message bodies, addresses or tokens.
        state, error = 'unknown', '处理异常，请检查配置与任务状态，未自动重发'
    db.execute('UPDATE mail_jobs SET state=?,error=?,next_at=?,finished_at=? WHERE id=?',
               (state, error, next_at, None if state == 'pending' else epoch, job['id']))
    db.commit()
    return state


def run_once(db, at=None, transport=None):
    if not configured():
        return 0
    at = beijing_time(at) if at is not None else datetime.now(TZ)
    enqueue_daily(db, at)
    count = 0
    while count < 200:
        current = at if transport else datetime.now(TZ)
        job = claim(db, current)
        if not job:
            break
        process_job(db, job, current if transport else datetime.now(TZ), transport)
        count += 1
    return count
