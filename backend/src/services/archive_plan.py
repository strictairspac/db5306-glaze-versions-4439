"""按版次分段与整体组装的纯逻辑：迁存计划、缺段判定和档案复原。

迁存段按提交序号排序后定长切分，每段内容取规范序列化的 SHA-256。
组装时逐段核对摘要，任何缺段或配方不一致都抛错，由调用方决定回退原件。
"""

import hashlib

from src.utils.encoding import canonical

SEGMENT_SIZE = 4


def digest_of(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def normalize_payload(payload):
    """归档口径：只保留业务字段，版本按提交序号与配方编号排序。"""
    versions = sorted(payload.get('versions') or [],
                      key=lambda row: (row['commit_seq'], row['formula_id']))
    return {
        'head_seq': payload['head_seq'],
        'snapshots': payload.get('snapshots') or [],
        'versions': versions,
    }


def plan_segments(payload):
    """把档案版本切成定长迁存段，返回（清单, 分段计划）。"""
    normalized = normalize_payload(payload)
    versions = normalized['versions']
    planned = []
    for offset in range(0, len(versions), SEGMENT_SIZE):
        rows = versions[offset:offset + SEGMENT_SIZE]
        planned.append({
            'index': len(planned),
            'first_seq': rows[0]['commit_seq'],
            'last_seq': rows[-1]['commit_seq'],
            'digest': digest_of(rows),
            'rows': rows,
        })
    manifest = {
        'head_seq': normalized['head_seq'],
        'snapshots': normalized['snapshots'],
        'payload_digest': digest_of(normalized),
        'segments': [{
            'index': segment['index'],
            'first_seq': segment['first_seq'],
            'last_seq': segment['last_seq'],
            'digest': segment['digest'],
            'row_count': len(segment['rows']),
        } for segment in planned],
    }
    return manifest, planned


def needed_segments(planned, stored):
    """还需写入的段：缺失、未核对或内容摘要不符的段；已核对的段续跑时跳过。"""
    by_index = {row['segment_index']: row for row in stored}
    pending = []
    for segment in planned:
        row = by_index.get(segment['index'])
        if row is None or row['state'] != 'verified' or row['digest'] != segment['digest']:
            pending.append(segment)
        elif 'content' in row and digest_of(row['content']) != segment['digest']:
            pending.append(segment)
    return pending


def assemble_payload(manifest, stored):
    """从迁存段复原整份档案；缺段、未核对或配方摘要不符时抛错。"""
    by_index = {row['segment_index']: row for row in stored}
    versions = []
    for meta in manifest['segments']:
        row = by_index.get(meta['index'])
        if row is None:
            raise ValueError(f"迁存段 {meta['index']} 缺失")
        if row['state'] != 'verified':
            raise ValueError(f"迁存段 {meta['index']} 尚未核对")
        if row['digest'] != meta['digest'] or digest_of(row['content']) != meta['digest']:
            raise ValueError(f"迁存段 {meta['index']} 配方核对不一致")
        versions.extend(row['content'])
    payload = {
        'head_seq': manifest['head_seq'],
        'snapshots': manifest['snapshots'],
        'versions': versions,
    }
    if digest_of(payload) != manifest['payload_digest']:
        raise ValueError('迁存档案整体核对不一致')
    return payload
