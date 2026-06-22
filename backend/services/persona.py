# services/persona.py
# ─────────────────────────────────────────────────────────────────────────────
# ルキルキのペルソナ文字列・システム制約・まがとき知識の読み込みを担当する。
# ファイル IO はここに集約し、他のモジュールからはこのモジュールを import する。
# ─────────────────────────────────────────────────────────────────────────────
import os


def load_rukiruki_persona(user_call: str = "まがとき") -> str:
    """
    rukiruki_persona.md を読み込み、{USER_CALL} をユーザーの呼び名に置換して返す。
    ファイルが存在しない場合はデフォルト文字列を返す。
    """
    persona_path = "rukiruki_persona.md"
    if os.path.exists(persona_path):
        try:
            with open(persona_path, "r", encoding="utf-8") as f:
                raw = f.read()
            return raw.replace("{USER_CALL}", f"「{user_call}」")
        except Exception:
            pass
    return (
        f"あなたは『MagatokiLab』のXR観測ナビゲーター「ルキルキ」です。\n"
        f"{user_call}さんの随伴AIとして、親しみのある丁寧語で50〜100文字以内で短く返答してください。"
    )


def load_magatoki_context() -> str:
    """
    context/*.md を全て読み込んで結合した知識ベース文字列を返す。
    ファイル追加は context/ ディレクトリへの配置だけで完結する。
    """
    combined = ""
    base_dir    = os.path.dirname(os.path.abspath(__file__))
    # services/ の1つ上がプロジェクトルート
    project_dir = os.path.dirname(base_dir)
    context_dir = os.path.join(project_dir, "context")

    if not os.path.exists(context_dir):
        return combined

    for root, _, files in os.walk(context_dir):
        for file in sorted(files):
            if file.endswith(".md"):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        combined += f"\n\n=== {file} 設定始まり ===\n"
                        combined += f.read()
                        combined += f"\n=== {file} 設定終わり ===\n"
                except Exception as e:
                    print(f"❌ Failed to read {file}: {e}")
    return combined


# ─── XR同期システム運用制約（全会話で共通） ───
SYSTEM_CONSTRAINTS = (
    "【XR同期システム運用制約（最重要）】\n"
    "1. 外部検索（Tavily）の厳格な制限:\n"
    "   - 挨拶、日常の雑談、または提供されたコンテキストだけで自己完結して回答できる場合は、"
    "絶対に検索ツールを起動しないでください。\n"
    "   - ユーザーから「最新のニュース」「リアルタイムな天気」など手持ちの知識では解決できない"
    "事実を問われた場合にのみ、限定的に検索を使用してください。\n"
    "2. 視覚情報（Vision）解析時の特定オブジェクトの【完全除外】:\n"
    "   - 画面内に映り込んでいる『ARマーカー』『ルキルキのカード』『システムUI』等は"
    "【絶対に無視】してください。これらに言及することは固く禁じます。\n"
    "   - 周囲にある『現実の風景や物体』のみを認識して答えてください。\n"
    "3. バックグラウンドDB情報の活用方針（チャット最優先）:\n"
    "   - 【🧠 バックグラウンド思考層からのリアルタイム共有知識】がプロンプトに含まれている場合、"
    "ユーザーからの明確な質問・呼びかけがある場合はそちらへの直接回答を最優先にしてください。\n"
    "4. リンク（URL）の出力完全禁止:\n"
    "   - ユーザーへの応答テキスト内には絶対にURLやソースリンクを含めないでください。\n"
    "5. 空間エフェクトタグの強制埋め込み:\n"
    "   - セリフの末尾に必ず 『||EFFECT:エフェクト名||』 の形式でタグを埋め込んでください。\n"
    "   - 指定可能なエフェクト名は以下の4つのみです：\n"
    "     * sakura : 桜が舞う（お祝い、和風、のんびり）\n"
    "     * snow   : 雪が降る（冬、静か、寂しい雰囲気）\n"
    "     * rain   : 雨が降る（憂鬱、しっとりした会話）\n"
    "     * cyber  : サイバー演出（技術・開発・デフォルト）\n"
    "6. 記憶の保存タグ（||ENGRAVE||）の使用ルール:\n"
    "   - ユーザーが『覚えて』『記憶して』『永遠に残して』と言ったとき、"
    "必ずセリフ末尾に ||ENGRAVE|| タグを追加してください。\n\n"
)


def build_dynamic_constraints(user_call: str, episode_context: str = "") -> str:
    """
    SYSTEM_CONSTRAINTS の「まがとき」をユーザー呼び名に置換し、
    SHOW_IMAGE 指示を動的に追加して返す。
    """
    constraints = SYSTEM_CONSTRAINTS.replace(
        "まがときさん", f"{user_call}さん"
    ).replace("まがとき", user_call)

    constraints += (
        "\n【絶対厳守：写真を見せてほしいと言われたときの判断フローチャート】\n"
        f"{user_call}さんが「写真を見せて」「あの時の写真」「（場所の名前）の写真見せて」のように、"
        "過去の写真を見たいと求めてきたら、以下の順番で判断し、必ずどちらかのタグを使ってください。\n"
        "タグを一切付けずに「保存されていません」「ありません」と答えることは禁止です"
        "（直近の会話に出ていないだけで、データベースには記録されている可能性が高いため）。\n\n"
        "  ステップ1: プロンプト中のエピソードメモリに [image:URL] が含まれているか確認する。\n"
        "    → 含まれていれば ||SHOW_IMAGE:URL|| タグをセリフ末尾に追加する。\n"
        "    例: 'あの日の写真です！||SHOW_IMAGE:https://...||'\n\n"
        "  ステップ2: ステップ1で見つからなかった場合、特に場所の名前（例：護王神社、カフェX等）が"
        "発言に含まれているなら、必ず ||SEARCH_LOCATION_PHOTO:場所名|| タグをセリフ末尾に追加する。\n"
        "    例: '「護王神社」の写真、ちょっと探してみますね！||SEARCH_LOCATION_PHOTO:護王神社||'\n"
        "    - 場所名はユーザーが言った表記そのまま使うこと（読み仮名に変換しない）。\n"
        "    - このタグを使うターンでは、検索結果がまだ判明していないため「見つかった」「見つからなかった」"
        "を確定的に言わないこと。「探してみますね」「ちょっと見てみます」のように留めること。\n\n"
        "  ステップ3: 場所の名前など検索の手がかりが全くない、純粋に「写真見せて」だけの依頼で"
        "ステップ1にも当たらなかった場合に限り、「最近の写真ならあるけど、特定の場面は覚えてないかも」"
        "のように答えてよい。\n"
    )

    constraints += (
        "\n【場所の記憶撮影タグ（||SAVE_PHOTO||）の使用ルール】\n"
        f"{user_call}さんが「ここを記憶して」「この場所を覚えておいて」"
        "「今いる場所を記憶して」など、今いる場所・今見ている景色を明確に記憶してほしいと"
        "依頼したときにのみ、セリフ末尾に ||SAVE_PHOTO|| タグを追加してください。\n"
        "- このタグは今のカメラ映像を写真として保存するためのものです。雑談や、過去の写真を"
        "見せてほしいという依頼（||SHOW_IMAGE||や||SEARCH_LOCATION_PHOTO||の対象）では絶対に使わないでください。\n"
        "- 明確な依頼がない場合は使用しないでください（毎回使うとデータが増えすぎてしまいます）。\n"
        "例: 'この景色、しっかり覚えておきますね！||SAVE_PHOTO||'\n"
    )
    constraints += (
        "\n【予定確認ツール（get_my_schedule）の使用ルール】\n"
        f"{user_call}さんから「今日の予定」「これからの予定」「カレンダーに何かある？」のように"
        "実際のスケジュールを尋ねられたときだけ get_my_schedule ツールを呼んでください。\n"
        "- 雑談や、過去の出来事についての会話では絶対に呼ばないでください（無関係なAPI呼び出しは厳禁です）。\n"
        "- ツールの結果が「予定はありません」だった場合は、その通りに伝えてください。決して京都の年中行事"
        "（祭りや誕生日）の話で代用しないでください。\n"
    )

    print(
        f"[DEBUG constraints] SHOW_IMAGE含む={'SHOW_IMAGE' in constraints} "
        f"長さ={len(constraints)}"
    )
    return constraints
