#!/usr/bin/env python3
"""测试问题生成器的改进"""

import time
from agents.problem_generator import generate_pattern_batch

print("=" * 60)
print("测试动态规划模式生成（dry_run=True）")
print("=" * 60)

start = time.time()

try:
    result = generate_pattern_batch(
        pattern_name='dynamic-programming',
        problem_count=5,
        dry_run=True,  # 不写文件，只测试 API 调用
    )

    elapsed = time.time() - start

    print("\n" + "=" * 60)
    print(f"✅ 测试完成！耗时: {elapsed:.1f} 秒")
    print(f"   成功: {len(result.problems)} 题")
    print(f"   失败: {len(result.errors)} 题")

    if result.problems:
        print("\n生成的题目:")
        for p in result.problems:
            print(f"  - LC-{p.leetcode_id}: {p.title} ({p.difficulty})")

    if result.errors:
        print("\n错误:")
        for err in result.errors:
            print(f"  - {err}")

    print("=" * 60)

except Exception as e:
    print(f"\n❌ 测试失败: {e}")
    import traceback
    traceback.print_exc()
