"""Small transition rules shared by task editing and progress uploads."""
from datetime import datetime

COMPLETION_FIELDS = ('status', 'progress', 'completed_at')
BLOCKER_FIELDS = ('blocker_reason', 'blocker_next_action', 'blocker_helper_id', 'blocker_follow_up')


def completion_values(prior, data, stamp):
    values = {key: prior.get(key) for key in COMPLETION_FIELDS}
    if not {'status', 'progress'}.intersection(data):
        return values
    if 'progress' not in data and data.get('status', prior['status']) == prior['status']:
        return values
    status = data.get('status', prior['status'])
    progress = data.get('progress', prior.get('progress', 0))
    if type(progress) is not int or not 0 <= progress <= 100:
        raise ValueError('进度必须是 0～100 的整数')
    if prior['status'] == '已取消':
        if 'status' not in data or status == '已取消':
            if 'progress' in data:
                raise ValueError('已取消任务请先明确重新打开，再更新进度')
        elif 'progress' not in data or progress == 100:
            raise ValueError('重新打开任务必须填写低于 100% 的实际进度')
    if status == '已完成' and 'status' in data:
        if 'progress' in data and progress != 100:
            raise ValueError('已完成与进度 100% 必须一致')
        progress = 100
    elif 'progress' in data and progress == 100:
        if 'status' in data and status != '已完成':
            raise ValueError('进度 100% 与已完成状态必须一致')
        status = '已完成'
    elif prior['status'] == '已完成':
        if 'progress' not in data or progress == 100:
            raise ValueError('重新打开任务必须填写低于 100% 的实际进度')
        if 'status' not in data:
            status = '进行中'
    if status != '已完成' and progress == 100:
        raise ValueError('非完成任务必须填写低于 100% 的实际进度')
    if status == '未开始' and progress > 0:
        status = '进行中'
    if prior['status'] == '阻塞' and status in ('未开始', '进行中'):
        status = '进行中'
    completed_at = prior.get('completed_at')
    if status != prior['status']:
        completed_at = stamp if status == '已完成' else None
    return {'status': status, 'progress': progress, 'completed_at': completed_at}


def blocker_values(db, prior, values, data):
    """Return current blocker fields and an optional immutable audit event."""
    fields = {key: prior.get(key, None if key == 'blocker_helper_id' else '') for key in BLOCKER_FIELDS}
    entering = values['status'] == '阻塞' and prior['status'] != '阻塞'
    editing = any(key in data and data[key] != fields[key] for key in BLOCKER_FIELDS)
    if values['status'] == '阻塞' and (entering or editing):
        for key in BLOCKER_FIELDS:
            if key in data:
                fields[key] = data[key]
        for key, label in (('blocker_reason', '阻塞原因'), ('blocker_next_action', '下一步')):
            raw = fields[key]
            if not isinstance(raw, str) or not raw.strip() or len(raw) > 2000:
                raise ValueError(label + '必填，最多 2000 字')
            fields[key] = raw.strip()
        helper = fields['blocker_helper_id']
        if helper in ('', None):
            helper = None
        elif type(helper) is not int or not 1 <= helper <= 2**63-1 or ((entering or helper != prior.get('blocker_helper_id')) and not db.execute(
            'SELECT 1 FROM members WHERE id=? AND is_active=1 AND deleted_at IS NULL', (helper,)
        ).fetchone()):
            raise ValueError('协助人不存在或已停用')
        fields['blocker_helper_id'] = helper
        follow_up = fields['blocker_follow_up'] or ''
        if not isinstance(follow_up, str):
            raise ValueError('跟进日期格式应为 YYYY-MM-DD')
        if follow_up:
            try:
                if len(follow_up) != 10:
                    raise ValueError()
                datetime.strptime(follow_up, '%Y-%m-%d')
            except ValueError:
                raise ValueError('跟进日期格式应为 YYYY-MM-DD') from None
        fields['blocker_follow_up'] = follow_up
        action = 'open' if entering else 'update'
        if entering or any(fields[key] != prior.get(key) for key in BLOCKER_FIELDS):
            return fields, {'action': action, **fields, 'resolution': ''}
    elif prior['status'] == '阻塞' and values['status'] != '阻塞':
        if values['status'] == '已完成':
            resolution = '任务进度达到 100%，自动完成并解除阻塞'
        elif values['status'] == '已取消':
            resolution = '任务已取消，结束阻塞跟进'
        else:
            resolution = data.get('blocker_result', data.get('resolution', ''))
            if not isinstance(resolution, str) or not resolution.strip() or len(resolution) > 2000:
                raise ValueError('解除阻塞必须填写处理结果，最多 2000 字')
            resolution = resolution.strip()
        event = {'action': 'cancel' if values['status'] == '已取消' else 'resolve', **fields, 'resolution': resolution}
        return {key: None if key == 'blocker_helper_id' else '' for key in BLOCKER_FIELDS}, event
    return fields, None


def record_blocker_event(db, task_id, actor, event, stamp):
    if event is None:
        return
    db.execute(
        'INSERT INTO task_blocker_events(task_id,actor_id,actor_name,action,reason,next_action,'
        'helper_id,follow_up,resolution,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
        (task_id, actor['id'], actor['name'], event['action'], event['blocker_reason'],
         event['blocker_next_action'], event['blocker_helper_id'], event['blocker_follow_up'],
         event['resolution'], stamp),
    )
