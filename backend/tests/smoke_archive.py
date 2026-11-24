"""分段迁存纯逻辑冒烟：断点续迁、缺段回退、整份组装与证据校验码不变。

运行：PYTHONPATH=. python3 -I tests/smoke_archive.py
只依赖标准库与 src.services.archive_plan / src.services.domain。
"""

import hashlib
import random

from src.services import domain
from src.services.archive_plan import (SEGMENT_SIZE, assemble_payload, digest_of,
                                       needed_segments, normalize_payload, plan_segments)
from src.utils.encoding import canonical

SAMPLE = domain.SPEC['sample']


def report_digest(payload):
    return hashlib.sha256(canonical(domain.solve(payload, None))).hexdigest()


def stored_from(planned, upto=None):
    """模拟已写入并核对的迁存段。"""
    chosen = planned if upto is None else planned[:upto]
    return [{
        'segment_index': segment['index'],
        'first_seq': segment['first_seq'],
        'last_seq': segment['last_seq'],
        'content': segment['rows'],
        'digest': segment['digest'],
        'state': 'verified',
    } for segment in chosen]


def expect_error(fn, needle):
    try:
        fn()
    except ValueError as exc:
        assert needle in str(exc), (needle, exc)
        return
    raise AssertionError(f'应当报错：{needle}')


def main():
    payload = {key: (list(value) if isinstance(value, list) else value)
               for key, value in SAMPLE.items()}

    # 分段计划：12 条版本按提交序号切成 3 段，清单摘要与规范化档案一致。
    manifest, planned = plan_segments(payload)
    assert len(planned) == 3, planned
    assert all(len(segment['rows']) <= SEGMENT_SIZE for segment in planned)
    seqs = [row['commit_seq'] for segment in planned for row in segment['rows']]
    assert seqs == sorted(seqs) == [5, 10, 12, 18, 24, 30, 36, 40, 58, 60, 72, 80]
    assert manifest['payload_digest'] == digest_of(normalize_payload(payload))

    # 行顺序打乱与重复计划生成相同清单（幂等）。
    shuffled = dict(payload)
    shuffled['versions'] = random.sample(payload['versions'], len(payload['versions']))
    again, _ = plan_segments(shuffled)
    assert again == manifest

    # 同笔提交涉及多个配方时按配方编号定序，结果仍然确定。
    tied = dict(payload)
    tied['versions'] = [
        {'formula_id': 'GL-B', 'commit_seq': 7, 'operation': '写入', 'recipe': '乙'},
        {'formula_id': 'GL-A', 'commit_seq': 7, 'operation': '写入', 'recipe': '甲'},
    ]
    first, _ = plan_segments(tied)
    second, _ = plan_segments(dict(tied, versions=list(reversed(tied['versions']))))
    assert first == second
    assert first['segments'][0]['digest'] == digest_of([
        {'formula_id': 'GL-A', 'commit_seq': 7, 'operation': '写入', 'recipe': '甲'},
        {'formula_id': 'GL-B', 'commit_seq': 7, 'operation': '写入', 'recipe': '乙'},
    ])

    # 整份组装：迁存段复原的档案与原件规范序列化一致。
    assembled = assemble_payload(manifest, stored_from(planned))
    assert canonical(assembled) == canonical(normalize_payload(payload))

    # 证据校验码不变：迁后重跑预检与迁前报告逐字节一致。
    assert report_digest(assembled) == report_digest(payload)
    assert report_digest(assembled) == report_digest(shuffled)

    # 断点续迁：逐段“提交”，中断后从未核对的段接着走，重试不重复写。
    stored = []
    for round_no in range(len(planned) + 1):
        pending = needed_segments(planned, stored)
        if not pending:
            break
        assert [segment['index'] for segment in pending] == list(range(round_no, 3))
        segment = pending[0]
        assert needed_segments(planned, stored) == pending  # 重试同一批，不重复
        stored = stored + stored_from(planned, round_no + 1)[round_no:]
    assert assemble_payload(manifest, stored)

    # 已核对段摘要漂移、内容被改或状态倒退时重新写入（续跑可自愈）。
    drifted = stored_from(planned)
    drifted[1] = {**drifted[1], 'digest': '0' * 64}
    assert [s['index'] for s in needed_segments(planned, drifted)] == [1]
    drifted = stored_from(planned)
    drifted[2] = {**drifted[2], 'state': 'written'}
    assert [s['index'] for s in needed_segments(planned, drifted)] == [2]
    tampered = stored_from(planned)
    tampered[0]['content'] = [dict(row, recipe='被篡改') for row in tampered[0]['content']]
    assert [s['index'] for s in needed_segments(planned, tampered)] == [0]

    # 缺段、未核对、配方对不上都拒绝组装（调用方回退原件，原件保留）。
    expect_error(lambda: assemble_payload(manifest, stored_from(planned, 2)), '缺失')
    unverified = stored_from(planned)
    unverified[0]['state'] = 'written'
    expect_error(lambda: assemble_payload(manifest, unverified), '尚未核对')
    corrupted = stored_from(planned)
    corrupted[1]['content'] = [dict(row, recipe='被篡改') for row in corrupted[1]['content']]
    expect_error(lambda: assemble_payload(manifest, corrupted), '配方核对不一致')
    bad_manifest = dict(manifest, payload_digest='0' * 64)
    expect_error(lambda: assemble_payload(bad_manifest, stored_from(planned)), '整体核对不一致')

    # 空版本档案：零段，直接组装。
    empty = {'head_seq': 3, 'snapshots': [], 'versions': []}
    empty_manifest, empty_planned = plan_segments(empty)
    assert empty_planned == []
    assert assemble_payload(empty_manifest, []) == empty

    print('smoke_archive: 全部通过')


if __name__ == '__main__':
    main()
