# agents/__init__.py
# ─────────────────────────────────────────────────────────────────────────────
# LangGraph マルチエージェント思考層パッケージ。
#
# 公開インターフェース:
#   from agents.graph  import build_rukiruki_graph
#   from agents.router import analyze_and_route
#
# nodes.py / keeper_agent.py は graph.py / router.py から内部的に import
# されるため、main.py から直接 import する必要はない。
# ─────────────────────────────────────────────────────────────────────────────
