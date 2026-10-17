"""Email-client-friendly HTML presentation for verification and task digests.

All variable content is escaped, and markup has no remote fonts, images or scripts.
The calling service remains responsible for recipient isolation and plain-text parts.
"""
from html import escape


def _e(value):
    return escape(str(value if value is not None else ''), quote=True)


def _progress(value, color='#3478d4'):
    try:
        amount = max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        amount = 0.0
    amount = round(amount, 2)
    label = f'{amount:g}%'
    fill = (f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'width="{amount:g}%" style="border-collapse:collapse;width:{amount:g}%">'
            f'<tr><td height="9" bgcolor="{color}" '
            f'style="background:{color};height:9px;font-size:1px;line-height:9px">'
            f'&nbsp;</td></tr></table>') if amount else ''
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse"><tr>'
            f'<td style="height:9px;background:#e9edf3;border-radius:4px;overflow:hidden">'
            f'{fill}</td></tr></table>'
            f'<p style="margin:5px 0 0;font-size:12px;color:#64748b;text-align:right">'
            f'进度 {label}</p>')


def _tag(value, *, urgent=False, complete=False):
    background, foreground = ('#fff0ec', '#b42318') if urgent else (
        ('#e5f7ef', '#147a4f') if complete else ('#e9f0ff', '#2657a4'))
    return (f'<span style="display:inline-block;margin:0 7px 6px 0;padding:3px 9px;'
            f'border-radius:4px;background:{background};color:{foreground};'
            f'font-size:12px;font-weight:600;line-height:20px">{_e(value)}</span>')


def _notice(text):
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="margin-top:12px;border-collapse:collapse">'
            f'<tr><td style="border-left:3px solid #e67c30;background:#fff7eb;'
            f'padding:10px 12px;color:#94501b;font-size:13px;line-height:1.6">'
            f'{_e(text)}</td></tr></table>')


def _link(url, text):
    return (f'<a href="{_e(url)}" style="display:inline-block;background:#285fc5;'
            f'color:#ffffff;text-decoration:none;border-radius:5px;'
            f'font-size:13px;font-weight:600;line-height:20px;'
            f'padding:9px 15px">{_e(text)} &rarr;</a>')


def _card_start():
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="width:100%;border-collapse:separate;border-spacing:0;'
            'margin:0 0 12px;background:#ffffff;border:1px solid #e3e9f1;'
            'border-radius:8px"><tr><td style="padding:17px 18px;'
            'overflow-wrap:anywhere;word-break:break-word">')


def _card_end():
    return '</td></tr></table>'


def _title(text):
    return (f'<h3 style="margin:0 0 11px;font-size:16px;line-height:1.5;'
            f'font-weight:700;color:#162842">{_e(text)}</h3>')


def _meta(label, value):
    return (f'<span style="display:inline-block;margin:0 14px 8px 0;'
            f'font-size:13px;line-height:1.55;color:#52647c">'
            f'{_e(label)}：<strong style="color:#263b56">{_e(value)}</strong></span>')


def _task_card(task, morning, progress_time):
    overdue = task['remaining'].startswith('已逾期')
    parts = [_card_start(), _title(task['title']),
             _tag(task['status'], complete=task['status'] == '已完成'),
             _tag(task['remaining'], urgent=overdue)]
    if morning:
        parts.append(_meta('您的角色', task['role']))
    if task['due']:
        parts.append(_meta('截止日期', task['due']))
    parts.append(_progress(task['progress']))
    if morning:
        parts.append(f'<p style="margin:12px 0 0;font-size:12px;line-height:1.6;'
                     f'color:#64748b">{_e(progress_time(task, "创建于 "))}</p>')
    if task['nudge']:
        parts.append(_notice('请更新进度：您负责的任务已超过 48 小时未更新。'))
    parts.extend([f'<p style="margin:16px 0 0">{_link(task["url"], "查看任务详情")}</p>',
                  _card_end()])
    return ''.join(parts)


def _project_card(project, morning, progress_time):
    overdue = project['remaining'].startswith('已逾期')
    parts = [_card_start(), _title(project['title']),
             _tag(project['status'], complete=project['status'] == '已完成'),
             _tag(project['remaining'], urgent=overdue)]
    if project['due']:
        parts.append(_meta('截止日期', project['due']))
    parts.extend([_progress(project['progress']),
                  f'<p style="margin:13px 0 8px;font-size:13px;font-weight:700;'
                  f'color:#334761">{"您负责的小组" if morning else "各小组进度"}</p>'])
    for group in project['groups']:
        parts.append('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                     'style="border-collapse:collapse;margin:0 0 9px"><tr><td '
                     'style="padding:8px 11px;background:#f5f7fb;'
                     'border-radius:5px;overflow-wrap:anywhere">')
        parts.append(f'<p style="margin:0 0 6px;font-size:13px;font-weight:600;'
                     f'color:#334761">{_e(group["label"])}组</p>')
        parts.append(_progress(group['progress'], '#20a17b'))
        if morning:
            parts.append(f'<p style="margin:6px 0 0;font-size:12px;'
                         f'color:#64748b">{_e(progress_time(group, "启用于 "))}</p>')
        if group['nudge']:
            parts.append(_notice(f'请更新进度：您负责的{group["label"]}组已超过 48 小时未更新。'))
        parts.append('</td></tr></table>')
    parts.extend([f'<p style="margin:16px 0 0">{_link(project["url"], "查看项目详情")}</p>',
                  _card_end()])
    return ''.join(parts)


def _section(title, count, contents):
    header = (f'<h2 style="margin:27px 0 12px;font-size:17px;line-height:1.5;'
              f'color:#162842">{_e(title)}'
              f'<span style="font-size:12px;font-weight:500;color:#718098">'
              f' &nbsp;{count} 项</span></h2>')
    return header + contents


def _empty(message):
    return (f'<p style="margin:0;padding:18px 15px;background:#ffffff;'
            f'border:1px dashed #d6dfea;border-radius:7px;color:#718098;'
            f'font-size:13px">{_e(message)}</p>')


def _document(title, eyebrow, subtitle, contents, preheader):
    # Presentation tables and inline styles are used intentionally: Gmail, 163
    # webmail and desktop email clients do not share modern web CSS support.
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light">'
        '<style>@media only screen and (max-width:600px){'
        '.mail-shell{width:100%!important}.mail-pad{padding:22px 16px!important}'
        '.mail-stat{padding:10px 6px!important}.mail-stat-number{font-size:19px!important}'
        '}</style></head>'
        '<body style="margin:0;padding:0;background:#f0f3f8;'
        'color:#21354f;font-family:Arial,Helvetica,sans-serif">'
        f'<div style="display:none!important;max-height:0;overflow:hidden;'
        f'opacity:0;color:transparent">{_e(preheader)}</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'bgcolor="#f0f3f8" style="border-collapse:collapse;width:100%;'
        'background:#f0f3f8"><tr><td align="center" style="padding:28px 10px">'
        '<table class="mail-shell" role="presentation" width="640" cellpadding="0" '
        'cellspacing="0" style="max-width:640px;width:100%;border-collapse:separate;'
        'border-spacing:0;background:#ffffff;border-radius:10px;overflow:hidden">'
        '<tr><td class="mail-pad" bgcolor="#162b4b" style="padding:29px 34px;'
        'background:#162b4b;color:#ffffff">'
        '<p style="margin:0 0 16px;font-size:13px;font-weight:700;'
        'letter-spacing:2px;color:#a9c5ed">巡天御风 <span style="font-weight:400;'
        'letter-spacing:1px">/ TEAM</span></p>'
        f'<p style="margin:0 0 7px;font-size:11px;letter-spacing:2px;'
        f'color:#b5c8e2">{_e(eyebrow)}</p>'
        f'<h1 style="margin:0;font-size:25px;font-weight:700;line-height:1.4;'
        f'color:#ffffff">{_e(title)}</h1>'
        f'<p style="margin:9px 0 0;font-size:13px;line-height:1.6;'
        f'color:#c7d7ed">{_e(subtitle)}</p>'
        '</td></tr>'
        '<tr><td class="mail-pad" style="padding:25px 34px 30px;'
        'background:#fafcff">'
        f'{contents}'
        '</td></tr><tr><td class="mail-pad" style="padding:20px 34px;'
        'border-top:1px solid #e5eaf2;background:#ffffff">'
        '<p style="margin:0;font-size:12px;line-height:1.8;color:#78869a">'
        '巡天御风团队管理平台 · 此邮件由系统自动发送，请勿直接回复。'
        '</p></td></tr></table>'
        '</td></tr></table></body></html>'
    )


def _stat(label, number):
    return (f'<td class="mail-stat" width="33%" align="center" '
            f'style="padding:13px 6px;border-right:1px solid #e5eaf2">'
            f'<p class="mail-stat-number" style="margin:0;font-size:23px;'
            f'font-weight:700;color:#174781">{number}</p>'
            f'<p style="margin:4px 0 0;font-size:12px;color:#718098">{_e(label)}</p></td>')


def render_digest_html(title, at, tasks, projects, morning, footer, progress_time):
    nudges = sum(bool(item['nudge']) for item in tasks) + sum(
        bool(group['nudge']) for project in projects for group in project['groups'])
    completed = sum(item['status'] == '已完成' for item in tasks + projects)
    third_label, third = ('需更新进度', nudges) if morning else ('今日完成', completed)
    stats = ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
             'style="width:100%;border-collapse:collapse;border:1px solid #e2e9f3;'
             'border-radius:7px;background:#ffffff"><tr>'
             + _stat('造车任务', len(tasks)) + _stat('日常项目', len(projects))
             + _stat(third_label, third) + '</tr></table>')
    task_html = ''.join(_task_card(task, morning, progress_time) for task in tasks)
    project_html = ''.join(_project_card(project, morning, progress_time) for project in projects)
    if not tasks:
        task_html = _empty('暂无您负责或参与的未完成造车任务' if morning else
                           '暂无团队未完成或今日完成的造车任务')
    if not projects:
        project_html = _empty('暂无您负责的未完成日常项目' if morning else
                              '暂无团队未完成或今日完成的日常项目')
    details = (stats
               + (_notice(f'有 {nudges} 处您负责的事项超过 48 小时未更新，请及时检查进度。')
                  if nudges else '')
               + _section('造车任务', len(tasks), task_html)
               + _section('日常项目', len(projects), project_html)
               + f'<p style="margin:25px 0 0;font-size:12px;'
               f'color:#73829b;line-height:1.8">{_e(footer)}</p>')
    subtitle = ('您的待办与进度概览' if morning else '全团队未完成事项与当天完成进展')
    date = at.strftime('%Y-%m-%d')
    return _document(title, '每日项目进度 / 北京时间', f'{date} · {subtitle}', details,
                     f'{title}｜造车任务 {len(tasks)} 项，日常项目 {len(projects)} 项')


def render_verification_html(code):
    details = ('<p style="margin:0 0 12px;font-size:15px;color:#344762">'
               '请输入以下 6 位验证码，完成邮箱绑定：</p>'
               '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
               'style="border-collapse:collapse"><tr><td align="center" '
               'style="padding:21px 10px;background:#ebf2fd;border:1px solid #d9e6fa;'
               'border-radius:8px">'
               f'<span style="font-size:32px;letter-spacing:7px;'
               f'font-weight:700;color:#1e4d95">{_e(code)}</span>'
               '</td></tr></table>'
               '<p style="margin:14px 0 12px;font-size:13px;color:#9b531b">'
               '验证码 30 分钟内有效，请勿转发给他人。</p>'
               '<p style="margin:0 0 12px;font-size:13px;line-height:1.8;'
               'color:#52647c">验证后每天北京时间 08:00 接收个人任务早报、'
               '22:00 接收团队项目晚报（包含周末）；无个人待办时跳过早报。</p>'
               '<p style="margin:0;font-size:12px;color:#718098">'
               '如非本人操作，请忽略此邮件。系统不会通过邮件向您索取密码。</p>')
    return _document('邮箱验证码', '账户安全 / 邮箱绑定',
                     '请前往网站个人账户页面输入验证码', details,
                     '您的邮箱绑定验证码已生成，30 分钟内有效')


def render_test_html():
    details = ('<p style="margin:0 0 13px;font-size:16px;font-weight:700;'
               'color:#1b654a">邮件模板预览成功</p>'
               '<p style="margin:0;font-size:14px;color:#52647c;line-height:1.8">'
               '这是一封测试邮件：当前没有团队未完成或今日完成事项。'
               '如果您能够正常看到这封邮件，说明邮件 HTML 样式已生成。'
               '请留意 SMTP 接受并不等于最终送达。</p>')
    return _document('邮件服务测试', '邮件通知 / 测试',
                     '用于检查邮箱显示效果', details,
                     '巡天御风邮件服务测试')
