"""Personal morning and team evening briefs with a durable SMTP queue."""
import hashlib
import hmac
import html
import os
import secrets
import smtplib
import ssl
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from urllib.parse import urlsplit

from flask import current_app
from modules.daily_projects import TZ, describe


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
    in_window = 8 <= at.hour < 12 if kind == 'morning' else at.hour >= 22
    return in_window and (day is None or day == at.date().isoformat())


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
    beginning = at.replace(hour=0, minute=0, second=0, microsecond=0)
    bounds = (beginning.strftime('%Y-%m-%d %H:%M:%S'),
              (beginning + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'))
    tasks = []
    task_filter = ("t.status<>'已完成' AND (t.owner_id=? OR EXISTS(SELECT 1 FROM task_participants tp "
                   "WHERE tp.task_id=t.id AND tp.member_id=?))" if morning else
                   "(t.status<>'已完成' OR (t.completed_at>=? AND t.completed_at<?))")
    for row in db.execute("SELECT t.*, (SELECT max(created_at) FROM task_progress_updates WHERE task_id=t.id) AS last_progress "
                          "FROM tasks t WHERE t.deleted_at IS NULL AND t.status<>'已取消' "
                          "AND " + task_filter + " "
                          "ORDER BY (t.status='已完成'),CASE WHEN t.due_date IS NULL OR t.due_date='' THEN 1 ELSE 0 END,t.due_date,t.id",
                          (mid, mid) if morning else bounds):
        owner = row['owner_id'] == mid
        last = row['last_progress'] or row['created_at']
        task = {'id': row['id'], 'title': row['title'], 'status': row['status'],
                'progress': row['progress'], 'due': row['due_date'], 'remaining': deadline(row['due_date'], at),
                'nudge': owner and row['status'] != '已完成' and stale(last, row['progress'], at),
                'url': settings()['base_url'] + f'/tasks?task={row["id"]}'}
        if morning:
            task.update(role='负责人' if owner else '参与者', last_progress=row['last_progress'],
                        reminder_baseline=last)
        tasks.append(task)
    projects = []
    project_filter = ("p.status<>'已完成' AND EXISTS(SELECT 1 FROM daily_owners o JOIN daily_groups g "
                      "USING(project_id,stage) WHERE o.project_id=p.id AND o.member_id=? AND g.enabled=1)"
                      if morning else "(p.status<>'已完成' OR (p.completed_at>=? AND p.completed_at<?))")
    rows = db.execute("SELECT p.* FROM daily_projects p WHERE p.deleted_at IS NULL AND p.status<>'已取消' "
                      "AND " + project_filter + " "
                      "ORDER BY (p.status='已完成'),CASE WHEN p.due_date IS NULL OR p.due_date='' THEN 1 ELSE 0 END,p.due_date,p.id",
                      (mid,) if morning else bounds)
    for row in rows:
        project = describe(db, row)
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
                         'url': settings()['base_url'] + f'/tasks?type=daily&project={row["id"]}'})
    return tasks, projects


def digest_content(db, mid, at, kind='daily'):
    tasks, projects = digest_items(db, mid, at, kind=kind)
    if not tasks and not projects:
        return None
    morning = kind == 'morning'
    title = '个人任务早报' if morning else '团队项目晚报'
    lines = [title, beijing_time(at).strftime('%Y-%m-%d'), '', '造车任务']
    cards = [f'<h1>{title}</h1>', '<h2>造车任务</h2>']
    def paragraph(text):
        cards.append('<p>' + html.escape(text) + '</p>')
    def progress_time(item, baseline_label):
        if item['last_progress']:
            return '最近进度更新：' + parsed_time(item['last_progress']).strftime('%Y-%m-%d %H:%M:%S')
        return '暂无进度更新（' + baseline_label + parsed_time(item['reminder_baseline']).strftime('%Y-%m-%d %H:%M:%S') + '）'
    if not tasks:
        lines.append('暂无您负责或参与的未完成造车任务' if morning else '暂无团队未完成或今日完成的造车任务'); paragraph(lines[-1])
    for task in tasks:
        text = f'{task["title"]}｜{task["status"]}｜{task["progress"]}%｜{task["remaining"]}'
        if task['due']:
            text += f'（{task["due"]}）'
        if morning:
            text += f'\n您的角色：{task["role"]}\n' + progress_time(task, '创建于 ')
        if task['nudge']:
            text += '\n请更新进度：您负责的任务已超过 48 小时未更新。'
        lines.extend([text, task['url'], '']); paragraph(text)
        cards.append(f'<p><a href="{html.escape(task["url"], quote=True)}">登录查看任务</a></p><hr>')
    lines.extend(['', '日常项目']); cards.append('<h2>日常项目</h2>')
    if not projects:
        lines.append('暂无您负责的未完成日常项目' if morning else '暂无团队未完成或今日完成的日常项目'); paragraph(lines[-1])
    for project in projects:
        text = f'{project["title"]}｜{project["status"]}｜{project["progress"]:g}%｜{project["remaining"]}'
        if project['due']:
            text += f'（{project["due"]}）'
        text += '\n' + ('您负责的小组：' if morning else '') + ' · '.join(f'{group["label"]} {group["progress"]}%' for group in project['groups'])
        for group in project['groups']:
            if morning:
                text += f'\n{group["label"]}组' + progress_time(group, '启用于 ')
            if group['nudge']:
                text += f'\n请更新进度：您负责的{group["label"]}组已超过 48 小时未更新。'
        lines.extend([text, project['url'], '']); paragraph(text)
        cards.append(f'<p><a href="{html.escape(project["url"], quote=True)}">登录查看项目</a></p><hr>')
    footer = ('截至发送时您负责或参与的未完成事项；每日北京时间 08:00 汇总，包含周末。时间均为北京时间。'
              if morning else '截至发送时的全团队未完成事项与北京时间当天完成事项；每日 22:00 汇总。')
    footer += '仅对您负责且超过 48 小时未更新的事项提示催更。如需暂停，请联系管理员。'
    lines.append(footer); paragraph(footer)
    markup = '<!doctype html><html lang="zh-CN"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="margin:0;padding:20px;background:#f5f5f7;color:#202024;font:16px/1.6 sans-serif"><main style="max-width:640px;margin:auto;background:#fff;padding:20px;overflow-wrap:anywhere;white-space:pre-line">' + ''.join(cards) + '</main></body></html>'
    return '\n'.join(lines), markup


def enqueue_daily(db, at):
    at = beijing_time(at)
    kind = next((kind for kind in ('morning', 'daily') if daily_window(at, kind=kind)), None)
    if kind is None:
        return
    day, epoch = at.date().isoformat(), at.timestamp()
    db.execute('BEGIN IMMEDIATE')
    try:
        for row in db.execute('SELECT e.* FROM member_email e JOIN members m ON m.id=e.member_id WHERE e.verified=1 AND e.paused=0 AND m.is_active=1 AND m.deleted_at IS NULL'):
            queue(db, row['member_id'], kind, day, row['generation'], epoch, f'{kind}:{day}:{row["member_id"]}')
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
        for kind, label, start, end in (('morning', '早报', 8, 12), ('daily', '晚报', 22, 24)):
            db.execute("UPDATE mail_jobs SET state='skipped',error=?,finished_at=? WHERE kind=? AND state='pending' "
                       "AND (day<>? OR ? < ? OR ? >= ?)",
                       (f'已超出当日{label}窗口', epoch, kind, at.date().isoformat(), at.hour, start, at.hour, end))
        row = db.execute("SELECT * FROM mail_jobs WHERE state='pending' AND next_at<=? ORDER BY CASE kind WHEN 'verify' THEN 0 WHEN 'test' THEN 1 ELSE 2 END,id LIMIT 1", (epoch,)).fetchone()
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
        if job['kind'] in ('morning', 'daily') and not daily_window(at, job['day'], kind=job['kind']):
            raise DeliveryError('已超出当日' + ('早报' if job['kind'] == 'morning' else '晚报') + '窗口', 'skipped')
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
            text = f'您的邮箱验证码是：{code}\n请在网站的个人账户页面输入验证码，30 分钟内有效。请勿转发给他人。\n验证后每天北京时间 08:00 接收个人任务早报、22:00 接收团队项目晚报（包含周末）；无个人待办时跳过早报。如非本人操作请忽略。'
            markup = ('<p>您的邮箱验证码是：</p><p style="font-size:30px;font-weight:700;letter-spacing:6px">' + code +
                      '</p><p>请在网站的个人账户页面输入验证码，30 分钟内有效。请勿转发给他人。</p>' +
                      '<p>验证后每天北京时间 08:00 接收个人任务早报、22:00 接收团队项目晚报（包含周末）；无个人待办时跳过早报。如非本人操作请忽略。</p>')
        else:
            if not member['verified'] or member['paused']:
                raise DeliveryError('邮箱未验证或提醒已暂停', 'skipped')
            content = digest_content(db, job['member_id'], at, kind=job['kind'])
            if not content and job['kind'] in ('morning', 'daily'):
                raise DeliveryError('没有个人未完成事项' if job['kind'] == 'morning' else '没有团队未完成或今日完成事项', 'skipped')
            text, markup = content or ('测试邮件：当前没有团队未完成或今日完成事项。', '<p>测试邮件：当前没有团队未完成或今日完成事项。</p>')
            subjects = {'morning': '巡天御风｜个人任务早报｜', 'daily': '巡天御风｜团队项目晚报｜'}
            message['Subject'] = subjects.get(job['kind'], '巡天御风｜测试邮件｜') + job['day']
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
                and (job['kind'] not in ('morning', 'daily')
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
