"""Personal digests and a durable SMTP queue. No worker starts on import."""
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
from modules.daily_projects import TZ, STAGES, describe


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


def verification_token(generation):
    return hmac.new(current_app.secret_key.encode(), ('email:' + generation).encode(), hashlib.sha256).hexdigest()


def queue(db, mid, kind, day, generation, at, key):
    db.execute('INSERT OR IGNORE INTO mail_jobs(member_id,kind,day,dedupe_key,generation,next_at,created_at) VALUES(?,?,?,?,?,?,?)',
               (mid, kind, day, key, generation, at, at))


def parsed_time(value):
    return datetime.fromisoformat(value).replace(tzinfo=TZ)


def stale(last, progress, at):
    return progress < 100 and (at - parsed_time(last)).total_seconds() > 72 * 3600


def deadline(due, at):
    if not due:
        return '未设置截止日期'
    days = (datetime.strptime(due, '%Y-%m-%d').date() - at.date()).days
    return f'已逾期 {-days} 天' if days < 0 else '今天截止' if days == 0 else f'剩余 {days} 天'


def digest_items(db, mid, at):
    tasks = []
    for row in db.execute("SELECT t.*, (SELECT max(created_at) FROM task_progress_updates WHERE task_id=t.id) AS last_progress "
                          "FROM tasks t WHERE t.deleted_at IS NULL AND t.status NOT IN ('已完成','已取消') "
                          "AND (t.owner_id=? OR EXISTS(SELECT 1 FROM task_participants p WHERE p.task_id=t.id AND p.member_id=?)) "
                          "ORDER BY CASE WHEN t.due_date IS NULL OR t.due_date='' THEN 1 ELSE 0 END,t.due_date,t.id", (mid, mid)):
        owner = row['owner_id'] == mid
        last = row['last_progress'] or row['created_at']
        tasks.append({'title': row['title'], 'status': row['status'], 'role': '负责人' if owner else '参与者',
                      'progress': row['progress'], 'due': row['due_date'], 'remaining': deadline(row['due_date'], at),
                      'last': row['last_progress'] or '尚未提交', 'nudge': owner and stale(last, row['progress'], at),
                      'url': settings()['base_url'] + f'/tasks?task={row["id"]}'})
    projects = []
    rows = db.execute("SELECT DISTINCT p.* FROM daily_projects p JOIN daily_groups g ON g.project_id=p.id "
                      "JOIN daily_owners o ON o.project_id=g.project_id AND o.stage=g.stage "
                      "WHERE p.deleted_at IS NULL AND p.status NOT IN ('已完成','已取消') AND g.enabled=1 AND o.member_id=? ORDER BY p.id", (mid,))
    for row in rows:
        project = describe(db, row)
        own_groups = []
        for group in project['groups']:
            if not group['enabled'] or mid not in [o['id'] for o in group['owners']]:
                continue
            last = group['latest']['created_at'] if group['latest'] else group['enabled_at']
            # Re-enabling a previously inactive group starts a new reminder interval.
            baseline = max(last, group['enabled_at'])
            own_groups.append({'label': group['label'], 'progress': group['progress'],
                               'last': group['latest']['created_at'] if group['latest'] else '尚未提交',
                               'nudge': stale(baseline, group['progress'], at)})
        projects.append({'title': row['title'], 'status': row['status'], 'groups': own_groups,
                         'due': row['due_date'], 'remaining': deadline(row['due_date'], at),
                         'url': settings()['base_url'] + f'/daily-projects/{row["id"]}'})
    return tasks, projects


def digest_content(db, mid, at):
    tasks, projects = digest_items(db, mid, at)
    if not tasks and not projects:
        return None
    lines = ['个人任务日报', at.strftime('%Y-%m-%d'), '', '造车任务']
    cards = ['<h1>个人任务日报</h1>', '<h2>造车任务</h2>']
    def paragraph(text):
        cards.append('<p>' + html.escape(text) + '</p>')
    if not tasks:
        lines.append('暂无个人未完成造车任务'); paragraph(lines[-1])
    for task in tasks:
        text = f'{task["title"]}｜{task["role"]}｜{task["status"]}｜{task["progress"]}%\n截止：{task["due"] or "未设置"} · {task["remaining"]}\n最近进度：{task["last"]}'
        if task['nudge']:
            text += '\n请更新进度：已超过 72 小时没有进度记录。'
        lines.extend([text, task['url'], '']); paragraph(text)
        cards.append(f'<p><a href="{html.escape(task["url"], quote=True)}">登录查看任务</a></p><hr>')
    lines.extend(['', '日常项目']); cards.append('<h2>日常项目</h2>')
    if not projects:
        lines.append('暂无本人负责的未完成日常项目'); paragraph(lines[-1])
    for project in projects:
        text = f'{project["title"]}｜{project["status"]}\n截止：{project["due"] or "未设置"} · {project["remaining"]}'
        for group in project['groups']:
            text += f'\n负责组别：{group["label"]} · {group["progress"]}% · 最近进度：{group["last"]}'
            if group['nudge']:
                text += ' · 请更新进度（超过 72 小时未更新）'
        lines.extend([text, project['url'], '']); paragraph(text)
        cards.append(f'<p><a href="{html.escape(project["url"], quote=True)}">登录查看项目</a></p><hr>')
    footer = '发送时的数据快照；每日上午 09:00 汇总。如需暂停每日邮件，请联系管理员。'
    lines.append(footer); paragraph(footer)
    markup = '<!doctype html><html lang="zh-CN"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="margin:0;padding:20px;background:#f5f5f7;color:#202024;font:16px/1.6 sans-serif"><main style="max-width:640px;margin:auto;background:#fff;padding:20px;overflow-wrap:anywhere;white-space:pre-line">' + ''.join(cards) + '</main></body></html>'
    return '\n'.join(lines), markup


def enqueue_daily(db, at):
    if not 9 <= at.hour < 12:
        return
    day, epoch = at.date().isoformat(), at.timestamp()
    db.execute('BEGIN IMMEDIATE')
    try:
        for row in db.execute('SELECT e.* FROM member_email e JOIN members m ON m.id=e.member_id WHERE e.verified=1 AND e.paused=0 AND m.is_active=1 AND m.deleted_at IS NULL'):
            queue(db, row['member_id'], 'daily', day, row['generation'], epoch, f'daily:{day}:{row["member_id"]}')
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
    epoch = at.timestamp()
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute("UPDATE mail_jobs SET state='unknown',error='发送进程中断，结果未知',finished_at=? WHERE state='sending' AND claimed_at<?", (epoch, epoch-1800))
        db.execute("UPDATE mail_jobs SET state='skipped',error='已超出当日日报窗口',finished_at=? WHERE kind='daily' AND state='pending' AND (day<>? OR ? >= 12 OR ? < 9)", (epoch, at.date().isoformat(), at.hour, at.hour))
        row = db.execute("SELECT * FROM mail_jobs WHERE state='pending' AND next_at<=? ORDER BY CASE kind WHEN 'verify' THEN 0 ELSE 1 END,id LIMIT 1", (epoch,)).fetchone()
        if row:
            db.execute("UPDATE mail_jobs SET state='sending',claimed_at=?,attempts=attempts+1 WHERE id=?", (epoch, row['id']))
        db.commit()
        return dict(row) | {'attempts': row['attempts']+1} if row else None
    except Exception:
        db.rollback(); raise


def process_job(db, job, at, transport=None):
    epoch, state, error, next_at = at.timestamp(), 'sent', '', at.timestamp()
    try:
        member = db.execute('SELECT e.*,m.is_active,m.deleted_at FROM member_email e JOIN members m ON m.id=e.member_id WHERE e.member_id=?', (job['member_id'],)).fetchone()
        if not member or not member['is_active'] or member['deleted_at'] or member['generation'] != job['generation']:
            raise DeliveryError('账户或邮箱已变化', 'skipped')
        message = EmailMessage()
        message['From'] = settings()['sender'] or 'preview@example.invalid'
        message['To'] = member['address']
        message['Message-ID'] = f'<xtyf-{job["id"]}-{job["generation"]}@{message["From"].split("@")[-1]}>'
        if job['kind'] == 'verify':
            if member['verified'] or member['expires_at'] <= epoch:
                raise DeliveryError('验证已完成或过期', 'skipped')
            token = verification_token(member['generation'])
            url = settings()['base_url'] + '/account/email/verify#token=' + token
            message['Subject'] = '巡天御风｜验证收件邮箱'
            text = f'请登录对应账户并确认绑定邮箱。链接自申请起 30 分钟有效：\n{url}\n验证后每天 09:00 接收个人任务日报；如非本人操作请忽略。'
            markup = '<p>请登录对应账户并确认绑定邮箱，链接自申请起 30 分钟有效。</p><p><a href="' + html.escape(url, quote=True) + '">确认绑定邮箱</a></p><p>验证后每天 09:00 接收个人任务日报。如非本人操作请忽略。</p>'
        else:
            if not member['verified'] or member['paused']:
                raise DeliveryError('邮箱未验证或提醒已暂停', 'skipped')
            content = digest_content(db, job['member_id'], at)
            if not content and job['kind'] == 'daily':
                raise DeliveryError('没有个人未完成事项', 'skipped')
            text, markup = content or ('测试邮件：当前没有个人未完成事项。', '<p>测试邮件：当前没有个人未完成事项。</p>')
            message['Subject'] = ('巡天御风｜个人任务日报｜' if job['kind'] == 'daily' else '巡天御风｜测试邮件｜') + job['day']
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
        if exc.retry and job['attempts'] < 3 and (job['kind'] != 'daily' or (at + timedelta(minutes=15)).hour < 12):
            state, next_at = 'pending', epoch + 900
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
    at = at or datetime.now(TZ)
    enqueue_daily(db, at)
    count = 0
    while count < 200:
        current = at if transport else datetime.now(TZ)
        job = claim(db, current)
        if not job:
            break
        process_job(db, job, current, transport)
        count += 1
    return count
