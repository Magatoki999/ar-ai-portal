# backend/agents/graph.py
"""
ルキルキ LangGraph グラフ定義。

フロー:
  router → agents(並列) → synthesizer → evaluator
                                              ↓
                               ok → END / retry → increment_retry → synthesizer
"""

from langgraph.graph import StateGraph, END

from agents.state import RukirukiState
from agents.nodes import (
    router_node,
    run_agents_parallel,
    synthesizer_node,
    evaluator_node,
    should_retry,
    increment_retry,
)


def build_rukiruki_graph():
    """
    ルキルキの思考グラフを構築して返す。
    main.py の lifespan で1回だけ呼び出してインスタンスを使い回す。
    """
    graph = StateGraph(RukirukiState)

    # ─── ノード登録 ───
    graph.add_node("router",         router_node)
    graph.add_node("agents",         run_agents_parallel)
    graph.add_node("synthesizer",    synthesizer_node)
    graph.add_node("evaluator",      evaluator_node)
    graph.add_node("increment_retry", increment_retry)

    # ─── エッジ（順序） ───
    graph.set_entry_point("router")
    graph.add_edge("router",      "agents")
    graph.add_edge("agents",      "synthesizer")
    graph.add_edge("synthesizer", "evaluator")

    # ─── 条件分岐：品質OK → END / 品質NG → 再試行 ───
    graph.add_conditional_edges(
        "evaluator",
        should_retry,
        {
            "ok":    END,
            "retry": "increment_retry"
        }
    )
    graph.add_edge("increment_retry", "synthesizer")

    return graph.compile()


# モジュール読み込み時にグラフをコンパイル
rukiruki_graph = build_rukiruki_graph()
