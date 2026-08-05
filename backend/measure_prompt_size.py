"""
measure_prompt_size.py

LLMに毎回送られるシステムプロンプト全体(ペルソナ+知識ベース+制約群)の
実際のサイズを、backend直下で実行して測定するスクリプト。

使い方:
    cd backend
    python measure_prompt_size.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.persona import load_rukiruki_persona, load_magatoki_context, build_dynamic_constraints

USER_CALL = "まがとき"

# episode_contextは実運用の目安として、それなりの長さのダミーを使う
dummy_episode_context = "🧠 最近の思い出:\n" + ("・2026-08-01 ダミーのエピソード内容です。\n" * 8)

persona_text = load_rukiruki_persona(USER_CALL)
magatoki_context = load_magatoki_context()
constraints = build_dynamic_constraints(USER_CALL, dummy_episode_context)

total = len(persona_text) + len(magatoki_context) + len(constraints)

print("=== システムプロンプト サイズ内訳 ===")
print(f"{len(persona_text):>7} 文字  ｜ load_rukiruki_persona (rukiruki_persona.md)")
print(f"{len(magatoki_context):>7} 文字  ｜ load_magatoki_context (context/*.md 全体)")
print(f"{len(constraints):>7} 文字  ｜ build_dynamic_constraints (ツールルール群、episode_context込み)")
print("-" * 50)
print(f"{total:>7} 文字  ｜ 合計")
print()
print(f"※ おおよそのトークン数目安(日本語は1文字≒1〜1.5トークン程度): "
      f"{int(total * 1.2):,} 〜 {int(total * 1.5):,} トークン前後")
