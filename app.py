# fmt: off
"""
混凝土减水剂配方 AI 助手
"""
import streamlit as st
import os, json, base64, datetime, math
from openai import OpenAI
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
FORMULA_DB = DATA_DIR / "formulas.jsonl"

st.set_page_config(page_title="混凝土配方AI助手", page_icon="🧪", layout="wide")
st.title("🧪 混凝土减水剂配方 AI 助手")
st.caption("知识引擎: JGJ 55-2011 配比计算 · PCE构效关系 · 14场景推荐 · 22种小料复配 · 全部国标体系")

@st.cache_data
def load_knowledge():
    kb_path = Path(__file__).parent / "综合知识库.md"
    if kb_path.exists():
        return kb_path.read_text(encoding="utf-8")
    return None

KNOWLEDGE_BASE = load_knowledge()

# OpenAI (Whisper语音 + GPT-4o图片)
api_key = st.sidebar.text_input("OpenAI API Key", type="password", placeholder="sk-... (语音/图片)")
client = OpenAI(api_key=api_key) if api_key else None

# DeepSeek (文字任务)
ds_client = OpenAI(api_key="sk-7162a8846afc4d9c8c6972fef7083abe", base_url="https://api.deepseek.com/v1")

@st.cache_data(ttl=5)
def load_formulas():
    if FORMULA_DB.exists():
        return [json.loads(line) for line in open(FORMULA_DB) if line.strip()]
    return []

formulas = load_formulas()
st.sidebar.metric("历史配方", len(formulas))

with st.sidebar.expander("🚀 部署分享"):
    st.markdown("**局域网**: http://192.168.1.77:8501\n\n**云部署**: GitHub + Streamlit Cloud")

def auto_val(key, default):
    af = st.session_state.get("auto_fill", {})
    val = af.get(key)
    if val and val != "None" and val != "":
        try:
            if key in ("sand_fm","mud_content","stone_dmax","target_slump","flyash_ratio","slag_ratio","temp_min","temp_max"):
                return float(val) if "." in str(val) else int(val)
        except:
            pass
        return val
    return default

# ═══════════════ 函数定义区 ═══════════════
def jgj55_water_dosage(slump_target, stone_type, dmax):
    base_table = {("碎石",10):205,("碎石",20):185,("碎石",40):170,("碎石",80):155,("卵石",10):190,("卵石",20):170,("卵石",40):160,("卵石",80):145}
    base = base_table.get((stone_type, dmax), 185)
    return base + max(0, (slump_target - 90)) / 20 * 5

def jgj55_water_binder_ratio(f_cu_k, sigma, stone_type, f_ce):
    alpha_a = 0.53 if stone_type == "碎石" else 0.49
    alpha_b = 0.20 if stone_type == "碎石" else 0.13
    f_cu_0 = f_cu_k + 1.645 * sigma
    return round((alpha_a * f_ce) / (f_cu_0 + alpha_a * alpha_b * f_ce), 3)

def sand_ratio(wb, dmax, sand_type, slump):
    base_table = {("碎石","粗砂",20):32,("碎石","中砂",20):35,("碎石","细砂",20):38,("卵石","粗砂",20):30,("卵石","中砂",20):33,("卵石","细砂",20):36,("碎石","粗砂",40):29,("碎石","中砂",40):32,("碎石","细砂",40):35}
    key = ("碎石" if "碎石" in stone_type else "卵石", sand_type, min(dmax,40))
    base_sr = base_table.get(key, 35)
    base_sr += (wb - 0.40) / 0.01 * 0.5
    if slump > 60:
        base_sr += (slump - 60) / 20 * 1.0
    return round(base_sr, 1)

def dosage_adjust_for_mud(base_dosage, mud_content, powder_content, mb_value=None):
    adjusted = base_dosage
    reasons = []
    if mud_content > 5.0:
        adjusted *= 1.3 + (mud_content - 5.0) * 0.05
        reasons.append(f"含泥量{mud_content}%较高，PCE被粘土大量吸附")
    elif mud_content > 3.0:
        adjusted *= 1.1 + (mud_content - 3.0) * 0.05
        reasons.append(f"含泥量{mud_content}%偏高，建议增加掺量补偿")
    elif mud_content > 2.0:
        adjusted *= 1.05
        reasons.append(f"含泥量{mud_content}%略高于正常")
    if powder_content > 8.0:
        adjusted *= 1.1
        reasons.append(f"石粉含量{powder_content}%较高")
    if mb_value:
        if mb_value < 0.5: pass
        elif mb_value < 1.0: adjusted *= 1.05; reasons.append(f"MB={mb_value}，轻微含泥")
        elif mb_value < 2.0: adjusted *= 1.15; reasons.append(f"MB={mb_value}，含泥明显")
        else: adjusted *= 1.3; reasons.append(f"MB={mb_value}偏高，建议冲洗骨料")
    return round(adjusted, 2), reasons

def dosage_adjust_for_temperature(base_dosage, temp):
    if temp < 10: return base_dosage * 1.0, "低温：加热拌合水"
    elif temp < 25: return base_dosage * 1.0, ""
    elif temp < 35: return base_dosage * 1.15, f"温度{temp}°C，掺量×1.15补偿坍损"
    else: return base_dosage * 1.4, f"高温{temp}°C，掺量×1.4"

def classify_problem(baseline_dosage, actual_dosage, observed_state, observed_slump, target_slump):
    slump_gap = target_slump - observed_slump
    problems, suggestions = [], []
    if observed_state in ["打不开","流动差","僵硬"] and slump_gap > 30:
        problems.append("A: 流动性不足")
        if actual_dosage < baseline_dosage * 1.3:
            suggestions.append(f"掺量从{actual_dosage}%提高至{round(baseline_dosage*1.2,2)}%-{round(baseline_dosage*1.3,2)}%")
        else:
            suggestions.append("掺量已较高仍打不开→检查C₃A/含泥量/母液适配")
    if observed_state in ["泌水","离析","过稀"]:
        problems.append("C: 离析/泌水")
        suggestions.append(f"可能过掺。建议降低掺量10-20%")
    if observed_state in ["急凝","假凝","超缓凝"]:
        problems.append("D: 凝结异常")
        suggestions.append("增加缓凝剂(葡钠0.05-0.15%)" if observed_state=="急凝" else "减少缓凝剂或加早强剂")
    if observed_state in ["包裹差","石子裸露","浆石分离"]:
        problems.append("F: 包裹性/和易性问题")
        suggestions.append("提高砂率2-4%或补加VMA增稠")
    if not problems: problems.append("状态基本正常"); suggestions.append("继续观测保坍")
    return problems, suggestions

def estimate_pce_type(mud_content, temp, target_slump, retention_hours):
    if not retention_hours: retention_hours = 1.0
    if mud_content > 4.0: return "抗泥型PCE + VMA", "0.20-0.35%"
    elif temp > 35: return "保坍缓凝型PCE", "0.25-0.40%"
    elif temp < 5: return "早强型PCE + 防冻剂", "0.20-0.30%"
    elif retention_hours > 1.5: return "保坍型PCE (长侧链)", "0.22-0.35%"
    elif target_slump >= 240: return "高减水PCE (短侧链高电荷) + VMA", "0.30-0.50%"
    else: return "标准型PCE (HPWR-S)", "0.18-0.25%"


# ═══════════════ 快速导航 ═══════════════
st.markdown("""
<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px;font-size:14px">
<a href="#prod-db" style="background:#d97756;color:white;padding:6px 12px;border-radius:4px;text-decoration:none">📚 生产配比库</a>
<a href="#materials" style="background:#4a90d9;color:white;padding:6px 12px;border-radius:4px;text-decoration:none">📋 原材料指标</a>
<a href="#pce" style="background:#28a745;color:white;padding:6px 12px;border-radius:4px;text-decoration:none">🧪 PCE复配</a>
<a href="#jgjjj" style="background:#6f42c1;color:white;padding:6px 12px;border-radius:4px;text-decoration:none">📐 JGJ 55计算</a>
<a href="#independent" style="background:#e83e8c;color:white;padding:6px 12px;border-radius:4px;text-decoration:none">📦 独立产品</a>
<a href="#share" style="background:#17a2b8;color:white;padding:6px 12px;border-radius:4px;text-decoration:none">🌐 数据共享</a>
</div>
""", unsafe_allow_html=True)

# ═══════════════ AI 知识库搜索 ═══════════════
st.markdown("---")
search_col1, search_col2, search_col3, search_col4 = st.columns([3, 1, 1, 1])
with search_col1:
    # 语音预填
    voice_val = st.session_state.get("kb_voice_text", "")
    kb_query = st.text_input("🔍 搜索知识库", value=voice_val, placeholder="打字或点🎤语音提问", key="kb_main_search", label_visibility="collapsed")
with search_col2:
    search_btn = st.button("🔍 搜索", key="kb_main_btn", use_container_width=True)
with search_col3:
    kb_audio = st.audio_input("🎤", key="kb_audio", label_visibility="collapsed")
with search_col4:
    if st.button("🗑️ 清除", key="kb_main_clear", use_container_width=True):
        st.session_state.kb_result = None
        st.session_state.kb_voice_text = ""
        st.rerun()

# 语音转文字
if kb_audio:
    if not client:
        st.error("语音识别需要 OpenAI API Key (左侧输入)")
    else:
        with st.spinner("🎙️ 转写中..."):
            ap = DATA_DIR / "kb_voice.mp3"
            ap.write_bytes(kb_audio)
            with open(ap, "rb") as af:
                t = client.audio.transcriptions.create(model="whisper-1", file=af, language="zh")
            st.session_state.kb_voice_text = t.text
            st.session_state.kb_result = None  # clear old result
            st.rerun()

if search_btn and kb_query:
    with st.spinner("正在搜索综合知识库(33KB)..."):
        # 发送完整知识库摘要 (精简版, 约3000字, 含核心数据和公式)
        kb = KNOWLEDGE_BASE if KNOWLEDGE_BASE else ""
        resp = ds_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role":"system","content":f"""你是混凝土配比和外加剂专家。用户问配合比相关问题时，必须给出一个完整的、可直接使用的混凝土配合比(水泥/粉煤灰/矿粉/砂/石/水/外加剂各多少kg/m³)，并附上水胶比、砂率、推荐外加剂掺量和配方。不要只给方法论，要给具体数字。引用知识库中的数据和公式。

以下是知识库的核心内容供参考:
{kb[:8000]}"""},
                {"role":"user","content":kb_query}
            ],
            max_tokens=4096,
            temperature=0.3
        )
        st.session_state.kb_result = resp.choices[0].message.content

if "kb_result" in st.session_state and st.session_state.kb_result:
    st.markdown("---")
    st.markdown(st.session_state.kb_result)

st.markdown("---")

# ═══════════════ 语音输入 ═══════════════
with st.expander("🎙️ 语音输入 · 说出配比参数自动填表", expanded=False):
    audio_col1, audio_col2 = st.columns([1, 2])
    with audio_col1:
        audio_bytes = st.audio_input("点击录音")
    with audio_col2:
        if audio_bytes and not client:
            st.error("请先在左侧输入 OpenAI API Key")
        elif audio_bytes and client:
            with st.spinner("🎙️ 正在转写语音..."):
                audio_path = DATA_DIR / "voice_input.mp3"
                audio_path.write_bytes(audio_bytes)
                with open(audio_path, "rb") as af:
                    transcript = client.audio.transcriptions.create(model="whisper-1", file=af, language="zh")
                spoken_text = transcript.text
                st.success(f"识别: **{spoken_text}**")
                with st.spinner("🧠 提取参数..."):
                    extract_prompt = f"""从语音文本提取混凝土配比参数。输出JSON。
文本: "{spoken_text}"
字段: cement_name, cement_type, sand_fm, mud_content, stone_type, stone_dmax, target_slump, strength_grade, temp_min, temp_max。未提到的填null。只输出JSON。"""
                    resp = ds_client.chat.completions.create(
                        model="deepseek-chat",
                        messages=[{"role":"user","content":extract_prompt}],
                        response_format={"type":"json_object"}, max_tokens=500
                    )
                    try:
                        st.session_state.voice_params = json.loads(resp.choices[0].message.content)
                        st.success("✅ 参数提取完成")
                    except:
                        st.error("参数解析失败")
    if "voice_params" in st.session_state and st.session_state.voice_params:
        params = st.session_state.voice_params
        vc1, vc2, vc3 = st.columns(3)
        with vc1:
            v_cement = st.text_input("水泥", value=str(params.get("cement_name") or ""), key="v_cement")
            v_sand_fm = st.text_input("细度模数", value=str(params.get("sand_fm") or ""), key="v_fm")
        with vc2:
            v_stone = st.text_input("石类型", value=str(params.get("stone_type") or ""), key="v_stone")
            v_mud = st.text_input("含泥量%", value=str(params.get("mud_content") or ""), key="v_mud")
        with vc3:
            v_slump = st.text_input("目标坍落度", value=str(params.get("target_slump") or ""), key="v_slump")
            v_grade = st.text_input("强度等级", value=str(params.get("strength_grade") or ""), key="v_grade")
        if st.button("📝 填入下方表格", type="primary"):
            st.session_state.auto_fill = {"cement_name":v_cement,"sand_fm":v_sand_fm,"stone_type":v_stone,"mud_content":v_mud,"target_slump":v_slump,"strength_grade":v_grade}
            st.rerun()

# ═══════════════ 生产配比学习库 ═══════════════
st.markdown('<div id="prod-db"></div>', unsafe_allow_html=True)
st.header("📚 生产配比学习库 (实战数据库)")

PROD_DB = DATA_DIR / "production_mixes.jsonl"
if "prod_mixes" not in st.session_state:
    st.session_state.prod_mixes = []
    if PROD_DB.exists():
        with open(PROD_DB) as f:
            st.session_state.prod_mixes = [json.loads(line) for line in f if line.strip()]

tab_prod, tab_region, tab_search = st.tabs(["📥 录入", "📊 区域分析", "🔍 搜索"])

with tab_prod:
    st.subheader("录入搅拌站实际生产使用的混凝土配比 + 外加剂配方")
    with st.form("prod_form"):
        st.markdown("**📋 混凝土配比 (每方用量)**")
        pc1, pc2, pc3, pc4 = st.columns(4)
        with pc1:
            prod_region = st.text_input("地区/城市 *", key="pf_region")
            prod_project = st.text_input("项目名称 *", key="pf_project")
            prod_grade = st.selectbox("强度等级", ["C20","C25","C30","C35","C40","C45","C50","C55","C60","C80"], key="pf_grade")
        with pc2:
            prod_cement_name = st.text_input("水泥厂家+品种", key="pf_cement")
            prod_cement_kg = st.number_input("水泥 kg/m³", 0, 600, 300, 5, key="pf_ckg")
            prod_fa_kg = st.number_input("粉煤灰 kg/m³", 0, 300, 60, 5, key="pf_fkg")
            prod_slag_kg = st.number_input("矿粉 kg/m³", 0, 300, 0, 5, key="pf_skg")
        with pc3:
            prod_sand_type = st.text_input("砂类型·细度", key="pf_sand")
            prod_sand_kg = st.number_input("砂 kg/m³", 0, 1200, 780, 5, key="pf_sakg")
            prod_stone_type = st.text_input("石类型·粒径", key="pf_stone")
            prod_stone_kg = st.number_input("石 kg/m³", 0, 1500, 1050, 5, key="pf_stkg")
        with pc4:
            prod_water = st.number_input("水 kg/m³", 100, 250, 165, 1, key="pf_wkg")
            prod_slump = st.number_input("出机坍落度 mm", 100, 280, 200, 5, key="pf_slump")

        # 自动计算
        binder = prod_cement_kg + prod_fa_kg + prod_slag_kg
        total_wt = binder + prod_sand_kg + prod_stone_kg + prod_water
        sand_r = round(prod_sand_kg / max(1, prod_sand_kg + prod_stone_kg) * 100, 1)
        wb = round(prod_water / max(1, binder), 3) if binder > 0 else 0
        ac1, ac2, ac3, ac4 = st.columns(4)
        ac1.metric("🧱 容重 kg/m³", f"{total_wt}")
        ac2.metric("🏖️ 砂率 %", f"{sand_r}")
        ac3.metric("🔗 胶材总量 kg", f"{binder}")
        ac4.metric("💧 水胶比", f"{wb}")

        st.markdown("**🧪 外加剂配方 (每吨成品)**")
        ad1, ad2, ad3 = st.columns(3)
        with ad1:
            prod_wr_name = st.text_input("减水母液型号", "PCE-WR", key="pf_wr")
            prod_wr_kg = st.number_input("母液 kg/吨", 0, 250, 120, 5, key="pf_wrkg")
        with ad2:
            prod_ret_name = st.text_input("保坍母液型号", "PCE-ST", key="pf_ret")
            prod_ret_kg = st.number_input("母液 kg/吨", 0, 200, 50, 5, key="pf_retkg")
        with ad3:
            prod_retarder = st.text_input("缓凝剂种类+用量", key="pf_retarder")
            prod_defoamer = st.number_input("消泡剂 kg/吨", 0.0, 3.0, 0.3, 0.1, key="pf_def")

        st.markdown("**📊 验证数据**")
        vd1, vd2, vd3 = st.columns(3)
        with vd1:
            prod_7d = st.number_input("7d强度 MPa", 0.0, 80.0, 0.0, 0.5, key="pf_7d")
        with vd2:
            prod_28d = st.number_input("28d强度 MPa", 0.0, 100.0, 0.0, 0.5, key="pf_28d")
        with vd3:
            prod_cost = st.number_input("单方成本 元/m³", 0, 800, 0, 5, key="pf_cost")
            prod_rating = st.selectbox("评分", [1,2,3,4,5], index=3, key="pf_rating")
        prod_notes = st.text_area("备注", key="pf_notes")

        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            eval_clicked = st.form_submit_button("🤖 AI 评价配比", type="secondary")
        with col_btn2:
            save_clicked = st.form_submit_button("💾 录入学习库", type="primary")

        if eval_clicked:
            kb = KNOWLEDGE_BASE[:3500] if KNOWLEDGE_BASE else ""
            eval_prompt = f"""基于混凝土配比和外加剂专业知识，评价以下配比并给出建议。

{chr(10).join(f'- {k}: {v}' for k,v in {
    "强度等级":prod_grade,"水泥":prod_cement_name,"水泥kg":prod_cement_kg,"粉煤灰kg":prod_fa_kg,
    "矿粉kg":prod_slag_kg,"砂":prod_sand_type,"砂kg":prod_sand_kg,"石":prod_stone_type,
    "石kg":prod_stone_kg,"水kg":prod_water,"坍落度mm":prod_slump,
    "减水母液":f"{prod_wr_name} {prod_wr_kg}kg/吨","保坍母液":f"{prod_ret_name} {prod_ret_kg}kg/吨",
    "缓凝剂":prod_retarder,"消泡剂kg":prod_defoamer,"7d强度":prod_7d,"28d强度":prod_28d,
    "水胶比":wb,"砂率":sand_r,"容重":total_wt,"胶材总量":binder
}.items() if v)}

知识库参考: {kb[:2000]}

请给出:
1. **配比评分** (1-5, 简述理由)
2. **关键问题** (如W/B偏高/偏低·砂率异常·胶材不足·强度不匹配等)
3. **外加剂配方评价** (母液比例·缓凝剂选择·小料用量是否合理)
4. **改进建议** (具体到数字)
5. **注意事项** (施工中可能遇到的问题)
格式简洁, 每条1-2句话。"""
            resp = ds_client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role":"system","content":"你是混凝土配比和外加剂专家。基于知识库评价配比。"},
                          {"role":"user","content":eval_prompt}],
                max_tokens=600
            )
            st.session_state.prod_eval = resp.choices[0].message.content

        if save_clicked:
            if not prod_project.strip() or not prod_region.strip():
                st.warning("地区和项目名称必填")
            else:
                record = {
                    "日期": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "地区": prod_region, "项目": prod_project, "强度等级": prod_grade,
                    "水泥": prod_cement_name, "水泥kg": prod_cement_kg,
                    "粉煤灰kg": prod_fa_kg, "矿粉kg": prod_slag_kg,
                    "砂": prod_sand_type, "砂kg": prod_sand_kg,
                    "石": prod_stone_type, "石kg": prod_stone_kg,
                    "水kg": prod_water, "坍落度": prod_slump,
                    "减水母液": prod_wr_name, "减水母液kg": prod_wr_kg,
                    "保坍母液": prod_ret_name, "保坍母液kg": prod_ret_kg,
                    "缓凝剂": prod_retarder, "消泡剂kg": prod_defoamer,
                    "7d强度": prod_7d if prod_7d>0 else None,
                    "28d强度": prod_28d if prod_28d>0 else None,
                    "单方成本": prod_cost if prod_cost>0 else None,
                    "评分": prod_rating, "备注": prod_notes,
                    "胶材总量": binder, "水胶比": wb, "砂率": sand_r, "容重": total_wt,
                }
                with open(PROD_DB, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                st.session_state.prod_mixes.append(record)
                st.balloons()
                st.success(f"✅ 已录入！学习库共 {len(st.session_state.prod_mixes)} 条")

    # 显示AI评价结果
    if "prod_eval" in st.session_state and st.session_state.prod_eval:
        st.markdown("---")
        st.markdown(st.session_state.prod_eval)
        if st.button("清除评价", key="clear_eval"):
            st.session_state.prod_eval = None
            st.rerun()

with tab_region:
    st.subheader("📊 区域分析 & AI推荐")
    if len(st.session_state.prod_mixes) >= 3:
        if st.button("🤖 AI 分析区域配比规律", type="primary"):
            with st.spinner("分析中..."):
                summary = "\n".join([f"- [{m.get('强度等级','')}] {m.get('项目','')} ({m.get('地区','')}): 水泥{m.get('水泥kg','')}kg 砂{m.get('砂kg','')}kg W/B={m.get('水胶比','?')} 28d={m.get('28d强度','?')}MPa" for m in st.session_state.prod_mixes[:20]])
                resp = ds_client.chat.completions.create(model="deepseek-chat", messages=[
                    {"role":"system","content":f"你是混凝土配比数据分析专家。参考以下知识库中的配比设计标准和外加剂复配经验来分析数据。{KNOWLEDGE_BASE[:2000] if KNOWLEDGE_BASE else ''}"},
                    {"role":"user","content":f"分析以下配比数据:\n{summary}\n请给出区域特征总结和C30/C40/C50推荐配方。"}], max_tokens=1000)
                st.markdown(resp.choices[0].message.content)
    else:
        st.info("学习库至少需要3条配比才能分析")

with tab_search:
    st.subheader("🔍 搜索历史配比")
    if st.session_state.prod_mixes:
        sg = st.selectbox("强度等级", ["全部"]+sorted(set(m.get("强度等级","") for m in st.session_state.prod_mixes if m.get("强度等级"))), key="sg")
        results = st.session_state.prod_mixes if sg=="全部" else [m for m in st.session_state.prod_mixes if m.get("强度等级")==sg]
        for m in results[-10:]:
            with st.expander(f"[{m.get('强度等级','')}] {m.get('项目','')} - {m.get('地区','')} | 28d={m.get('28d强度','?')}MPa"):
                st.markdown(f"水泥{m.get('水泥kg','')}+FA{m.get('粉煤灰kg','')}+SL{m.get('矿粉kg','')} | 砂{m.get('砂kg','')} 石{m.get('石kg','')} | 水{m.get('水kg','')} W/B={m.get('水胶比','?')}")
                st.markdown(f"外加剂: 减水{m.get('减水母液kg','?')}kg/吨 保坍{m.get('保坍母液kg','?')}kg/吨 | 容重{m.get('容重','?')} 砂率{m.get('砂率','?')}%")
                if m.get('备注'): st.caption(m.get('备注'))

st.markdown(f"📊 共 {len(st.session_state.prod_mixes)} 条配比")

# ═══════════════ 原材料指标 ═══════════════
st.markdown("---")
st.markdown('<div id="materials"></div>', unsafe_allow_html=True)
st.header("Step 1: 原材料技术指标输入")

tab_mat = st.tabs(["🏗️ 水泥", "🪨 骨料", "🌋 掺合料", "🧪 外加剂 & 水", "📊 汇总"])

with tab_mat[0]:
    st.markdown("### 水泥 (GB 175)")
    c1, c2 = st.columns(2)
    with c1:
        cement_name = st.text_input("水泥厂家/品牌", auto_val("cement_name","峨胜 P.O 42.5"))
        cement_type = st.selectbox("水泥品种", ["P.O 42.5","P.O 42.5R","P.O 52.5","P.I 42.5","P.II 42.5"])
    with c2:
        f_ce_g = st.number_input("水泥强度等级值 f_ce,g (MPa)", 32.5, 52.5, 42.5, 2.5)
        gamma_c = st.number_input("水泥富余系数 γ_c", 1.00, 1.20, 1.06, 0.01)
        f_ce = gamma_c * f_ce_g
        st.metric("水泥28d实测强度 f_ce", f"{f_ce:.1f} MPa")
        cement_c3a = st.number_input("C₃A含量 % (可选)", 0.0, 15.0, 0.0, 0.1)
        rho_c = st.number_input("水泥密度 kg/m³", 2800, 3300, 3100, 10)

with tab_mat[1]:
    st.markdown("### 砂 (GB/T 14684)")
    s1, s2 = st.columns(2)
    with s1:
        sand_source = st.selectbox("砂来源", ["河沙","机制砂","混合砂"])
        sand_type = st.selectbox("砂粗细", ["粗砂 (FM 3.7-3.1)","中砂 (FM 3.0-2.3)","细砂 (FM 2.2-1.6)"])
        sand_fm = st.number_input("细度模数 FM", 1.0, 4.0, auto_val("sand_fm",2.8), 0.1)
    with s2:
        sand_mud = st.number_input("含泥量 %", 0.0, 15.0, auto_val("mud_content",3.0), 0.1)
        sand_mb = st.number_input("MB值 (机制砂必测)", 0.0, 5.0, 0.0, 0.1)
        sand_powder = st.number_input("石粉含量 %", 0.0, 30.0, 5.0, 0.1)
        rho_sand = st.number_input("砂表观密度 kg/m³", 2500, 2800, 2650, 10)

    st.markdown("### 石 (GB/T 14685)")
    st1, st2 = st.columns(2)
    with st1:
        stone_type = st.selectbox("石料类型", ["碎石 (石灰岩)","碎石 (花岗岩)","碎石 (玄武岩)","卵石"])
        stone_dmax = st.selectbox("最大粒径 Dmax mm", [16,20,25,31.5,40], index=[16,20,25,31.5,40].index(auto_val("stone_dmax",25)) if auto_val("stone_dmax",25) in [16,20,25,31.5,40] else 2)
    with st2:
        stone_mud = st.number_input("石含泥量 %", 0.0, 5.0, 1.0, 0.1)
        stone_flaky = st.number_input("针片状含量 %", 0, 25, 8, 1)
        rho_stone = st.number_input("石表观密度 kg/m³", 2500, 3000, 2700, 10)

with tab_mat[2]:
    st.markdown("### 粉煤灰 (GB/T 1596)")
    use_flyash = st.checkbox("使用粉煤灰", True)
    fa_grade = st.selectbox("等级", ["I级","II级","III级"], disabled=not use_flyash)
    fa_ratio = st.number_input("掺量 %", 0, 50, 20, 5, disabled=not use_flyash)
    fa_loi = st.number_input("烧失量 %", 0.0, 15.0, 3.5, 0.1, disabled=not use_flyash)
    fa_water_demand = st.number_input("需水量比 %", 90, 120, 102, 1, disabled=not use_flyash)
    rho_fa = st.number_input("密度 kg/m³", 1800, 2600, 2200, 10, disabled=not use_flyash)
    gamma_f = st.number_input("影响系数 γ_f", 0.60, 1.00, 0.80, 0.05, disabled=not use_flyash)

    st.markdown("### 矿粉 (GB/T 18046)")
    use_slag = st.checkbox("使用矿粉")
    slag_grade = st.selectbox("等级", ["S75","S95","S105"], disabled=not use_slag)
    slag_ratio = st.number_input("掺量 %", 0, 60, 0, 5, disabled=not use_slag)
    slag_7d = st.number_input("7d活性指数 %", 50, 120, 80, 1, disabled=not use_slag)
    slag_28d = st.number_input("28d活性指数 %", 70, 130, 100, 1, disabled=not use_slag)
    rho_slag = st.number_input("密度 kg/m³", 2600, 3100, 2900, 10, disabled=not use_slag)
    gamma_s = st.number_input("影响系数 γ_s", 0.80, 1.20, 0.95, 0.05, disabled=not use_slag)

with tab_mat[3]:
    st.markdown("### 外加剂 (GB 8076)")
    pce_name = st.text_input("型号", "PCE标准型")
    pce_type = st.selectbox("类型", ["HPWR-A 标准型","HPWR-S 缓凝型","HPWR-R 早强型","保坍型(长侧链)","抗泥型"])
    pce_solid = st.number_input("含固量 %", 5.0, 50.0, 20.0, 0.5)
    pce_rate = st.number_input("减水率 %", 15, 45, 28, 1)
    pce_rec = st.number_input("推荐掺量下限 %", 0.1, 2.0, 0.15, 0.05)

with tab_mat[4]:
    st.markdown("### 原材料汇总")
    st.markdown(f"| 水泥 | {cement_name} | f_ce={f_ce:.1f}MPa C₃A={cement_c3a}% |")
    st.markdown(f"| 砂 | {sand_source} {sand_type} | FM={sand_fm} 含泥={sand_mud}% MB={sand_mb} |")
    st.markdown(f"| 石 | {stone_type} | Dmax={stone_dmax}mm 含泥={stone_mud}% |")
    if use_flyash: st.markdown(f"| 粉煤灰 | {fa_grade} {fa_ratio}% | 烧失={fa_loi}% 需水比={fa_water_demand}% |")
    if use_slag: st.markdown(f"| 矿粉 | {slag_grade} {slag_ratio}% | 7d活性={slag_7d}% 28d活性={slag_28d}% |")
    st.markdown(f"| 外加剂 | {pce_name} {pce_type} | 含固{pce_solid}% 减水率{pce_rate}% |")


# ═══════════════ PCE关键指标 ═══════════════
st.markdown("---")
st.subheader("🔑 影响PCE配方的关键原材料指标")
st.caption("以下指标直接决定减水剂母液选择、掺量范围和复配方案。")

kc1, kc2, kc3, kc4 = st.columns(4)
with kc1:
    st.metric("🏭 水泥品牌", cement_name)
    st.metric("💪 f_ce", f"{f_ce:.1f} MPa")
    st.metric("🧪 C₃A含量", f"{cement_c3a if cement_c3a>0 else '未测'}%")
    st.caption("C₃A>8%→掺量×1.2; >10%→×1.35")
with kc2:
    st.metric("🏖️ 砂 FM", f"{sand_fm}")
    st.metric("🧱 含泥量", f"{sand_mud}%")
    st.metric("📏 MB值", f"{sand_mb if sand_mb>0 else '未测'}")
    st.caption("含泥>3%→掺量×1.2; MB>1.4→抗泥母液")
with kc3:
    st.metric("🌋 粉煤灰 掺量", f"{fa_ratio if use_flyash else 0}%")
    st.metric("💧 需水量比", f"{fa_water_demand if use_flyash else '-'}%")
    st.metric("🔥 烧失量", f"{fa_loi if use_flyash else '-'}%")
    st.caption("需水量比>105%→用水量增加")
with kc4:
    st.metric("⛰️ 石类型", stone_type)
    st.metric("⚙️ 矿粉 掺量", f"{slag_ratio if use_slag else 0}%")
    st.metric("📊 矿粉28d活性", f"{slag_28d if use_slag else '-'}%")
    st.caption("花岗岩最省减水剂; 玄武岩需+10~20%")

# 详细影响注释
with st.expander("📖 关键指标超限影响 & 不同材料类型差异", expanded=False):
    tab_n1, tab_n2, tab_n3 = st.tabs(["🏭 水泥·掺合料", "🏖️ 砂·含泥·MB", "⛰️ 石·温度"])

    with tab_n1:
        st.markdown("""
**C₃A含量** | 4~6%:✅理想 | 6~8%:⚠️掺量×1.1~1.2 | 8~10%:🔴掺量×1.2~1.3+缓凝 | >10%:🔴×1.3~1.5+换水泥
**机理**: C₃A带正电荷→吸附PCE分子→溶液中有效减水剂浓度骤降→坍损加速

**粉煤灰需水量比** | ≤95%:✅I级·掺量最低 | 95~105%:⚠️II级·正常 | 105~115%:🔴III级·掺量大幅升高 | >115%:🔴劣质·28d强度降20%
**烧失量**: 每+1%→碳吸附PCE约+0.05%掺量。I级≤5%,II级≤8%,III级≤15%

**矿粉活性** | S105(28d≥105%):✅ | S95(≥95%):✅ | S75(≥75%):⚠️活性偏低·不推荐高标号
""")

    with tab_n2:
        st.markdown("""
**含泥量** — 对PCE影响最大的骨料指标 | <2%:✅正常 | 2~3%:⚠️↓10~15%×1.05~1.10 | 3~5%:🔴↓20~30%×1.10~1.25 | 5~8%:🔴↓40~50%×1.30~1.50 | >8%:PCE基本失效
**机理**: 蒙脱土插层吸附PCE侧链(PEO)+边缘吸附主链(-COO⁻)→PCE大量消耗

**MB值** — 区分泥和粉的黄金指标 | <0.5:纯石粉✅ | 0.5~1.0:轻微⚠️ | 1.0~1.4:含黏土🔴×1.15 | 1.4~2.0:黏土高🔴×1.30 | >2.0:建议水洗

**机制砂 vs 河沙**: 机制砂棱角粗糙→和易性差·需更多浆体·PCE掺量通常+10~20%·砂率+2~4%
""")

    with tab_n3:
        st.markdown("""
**石料类型** | **花岗岩**:✅工作性最好·后期强度最高·掺量最低 | **石灰岩**:⚠️居中·早期强度高(Ca促水化)·后期偏低 | **玄武岩**:🔴最差·多气孔·掺量+10~20%

**工作性排序**: 花岗岩>石灰岩>玄武岩 | **早期强度(3d)**: 石灰岩>花岗岩>玄武岩 | **后期强度(28d)**: 花岗岩>玄武岩>石灰岩

**温度范围** | 15~25°C:✅最佳 | 25~35°C:⚠️×1.15+缓凝+30% | >35°C:🔴×1.4+缓凝×1.5~2.0+保坍母液↑ | <5°C:早强型PCE
**温差>15°C**: 取高温端设计(保守防坍损)·取低温端判断防冻
""")

# ═══════════════ 项目要求 + JGJ 55 ═══════════════

st.markdown("---")
st.subheader("🎯 项目技术要求 & 环境条件")
pc1, pc2, pc3 = st.columns(3)
with pc1:
    strength_grade = st.selectbox("强度等级", ["C15","C20","C25","C30","C35","C40","C45","C50","C55","C60","C80"], index=5)
    target_slump = st.number_input("目标坍落度 mm", 50, 280, auto_val("target_slump",200), 10)
with pc2:
    temp_col1, temp_col2 = st.columns(2)
    with temp_col1:
        temp_min = st.number_input("最低温 °C", -10, 50, 15, 1)
    with temp_col2:
        temp_max = st.number_input("最高温 °C", -10, 50, 35, 1)
    temp = temp_max
    construction = st.selectbox("施工方式", ["泵送","吊斗","自卸","自密实","水下"])
with pc3:
    transport_time = st.number_input("运输时间 min", 0, 180, 45, 5)
    retention_req = st.number_input("保坍时间 h", 0.5, 4.0, 2.0, 0.5)

st.markdown("---")
st.markdown('<div id="jgjjj"></div>', unsafe_allow_html=True)
st.header("📐 JGJ 55-2011 配合比计算")

if st.button("🔢 执行JGJ 55计算", type="primary"):
    f_cu_k = float(strength_grade[1:])
    sigma_dict = {15:3.5,20:4.0,25:5.0,30:5.0,35:5.0,40:5.0,45:6.0,50:6.0,55:6.0,60:6.0,70:6.0,80:6.0}
    sigma = sigma_dict.get(f_cu_k, 5.0)
    f_cu_0 = f_cu_k + 1.645 * sigma

    is_crushed = "碎石" in stone_type
    alpha_a, alpha_b = (0.53, 0.20) if is_crushed else (0.49, 0.13)

    f_b = f_ce
    if use_flyash: f_b *= gamma_f
    if use_slag: f_b *= gamma_s

    wb_calc = (alpha_a * f_b) / (f_cu_0 + alpha_a * alpha_b * f_b)
    max_wb = 0.60
    if f_cu_k >= 50: max_wb = 0.50
    if f_cu_k >= 60: max_wb = 0.45
    wb_final = min(wb_calc, max_wb)

    stone_key = "碎石" if is_crushed else "卵石"
    water_table = {(stone_key,16):195,(stone_key,20):185,(stone_key,25):180,(stone_key,31.5):175,(stone_key,40):170}
    m_w0_base = water_table.get((stone_key, stone_dmax), 185)
    slump_adj = max(0, (target_slump - 90)) / 20 * 5
    m_w0 = m_w0_base + slump_adj

    m_b0 = max(m_w0 / wb_final, 260)
    m_fa = m_b0 * fa_ratio / 100 if use_flyash else 0
    m_slag = m_b0 * slag_ratio / 100 if use_slag else 0
    m_c = m_b0 - m_fa - m_slag

    sand_coarse = "粗砂" if "粗" in sand_type else ("细砂" if "细" in sand_type else "中砂")
    sr_key = (stone_key, sand_coarse, min(stone_dmax, 40))
    sr_table = {(stone_key,"粗砂",20):33,(stone_key,"中砂",20):36,(stone_key,"细砂",20):39,(stone_key,"粗砂",40):30,(stone_key,"中砂",40):33,(stone_key,"细砂",40):36}
    sr = sr_table.get(sr_key, 35)
    sr += (wb_final - 0.40) / 0.01 * 0.5
    if target_slump > 60: sr += (target_slump - 60) / 20 * 1.0
    if sand_source in ["机制砂","混合砂"]: sr += 2

    v_water = m_w0 / 1000
    v_cement = m_c / rho_c
    v_fa_vol = m_fa / rho_fa if m_fa > 0 else 0
    v_slag_vol = m_slag / rho_slag if m_slag > 0 else 0
    v_paste = v_water + v_cement + v_fa_vol + v_slag_vol
    air = 1.5
    v_agg = 1.0 - v_paste - air/100
    v_sand = v_agg * sr / 100
    v_stone = v_agg - v_sand
    m_sand = v_sand * rho_sand
    m_stone = v_stone * rho_stone

    base_dosage = pce_rec
    if sand_mud > 5.0: base_dosage *= 1.35
    elif sand_mud > 3.0: base_dosage *= 1.20
    elif sand_mud > 2.0: base_dosage *= 1.08
    if cement_c3a > 10: base_dosage *= 1.35
    elif cement_c3a > 8: base_dosage *= 1.20
    if temp > 35: base_dosage *= 1.4
    elif temp > 25: base_dosage *= 1.15
    rec_dosage = min(base_dosage, 0.50)
    m_ad = m_b0 * rec_dosage / 100

    m_cp = m_c + m_fa + m_slag + m_sand + m_stone + m_w0 + m_ad

    st.success(f"✅ JGJ 55 计算完成")

    # 详细计算过程 (可展开)
    with st.expander("📐 详细计算过程 (JGJ 55-2011 条款对照)", expanded=False):
        st.markdown(f"""
**步骤1: 配制强度 (§4.0.1-4.0.2)**
- f_cu,k = {f_cu_k} MPa · σ = {sigma} MPa (查JGJ 55表4.0.2)
- f_cu,0 = f_cu,k + 1.645σ = **{f_cu_0:.1f} MPa**

**步骤2: 水胶比 (§5.1)**
- f_ce = γ_c × f_ce,g = {gamma_c} × {f_ce_g} = **{f_ce:.1f} MPa** (§5.1.2)
- α_a={alpha_a}, α_b={alpha_b} ({stone_key} §5.1.1)
- Bolomy: W/B = (α_a×f_ce)/(f_cu,0+α_a×α_b×f_ce) = **{wb_calc:.3f}**
- 耐久性校核上限={max_wb} → 最终W/B = **{wb_final:.3f}**

**步骤3: 用水量 (§5.2)**
- 基准({stone_key} Dmax{stone_dmax}mm): {m_w0_base} kg/m³ (查表5.2.1)
- 坍落度修正(+{slump_adj:.0f}kg) → m_w0 = **{m_w0:.0f} kg/m³**

**步骤4: 胶材用量 (§5.3)**
- m_b0 = m_w0/(W/B) = **{m_b0:.0f} kg/m³** (≥260最小胶材✓)
- 水泥{m_c:.0f} · 粉煤灰{m_fa:.0f}({fa_ratio}%) · 矿粉{m_slag:.0f}({slag_ratio}%)

**步骤5: 砂率 (§5.4)**
- 基准: {sr_key[1]} Dmax{min(stone_dmax,40)} → 查表 → W/B修正+{sr-35:.1f}% → **β_s = {sr:.1f}%**

**步骤6: 骨料用量 (§5.5.2 绝对体积法)**
- 浆体体积 {v_paste:.4f}m³ + 含气量{air}%
- 骨料总体积 {v_agg:.4f}m³ → 砂**{m_sand:.0f}kg** · 石**{m_stone:.0f}kg**

**步骤7: 外加剂推荐**
- 基础{pce_rec}% → 含泥量/温度/C₃A修正 → **{rec_dosage:.2f}%** · 用量**{m_ad:.1f}kg/m³**
""")

    # 配比结果表
    st.markdown(f"""
| 组分 | 用量 (kg/m³) | 比例 |
|------|-------------|------|
| 水泥 ({cement_name}) | {m_c:.0f} | 1.00 |
| 粉煤灰 ({fa_grade if use_flyash else '-'}) | {m_fa:.0f} | {m_fa/m_c:.2f} |
| 矿粉 ({slag_grade if use_slag else '-'}) | {m_slag:.0f} | {m_slag/m_c:.2f} |
| 砂 | {m_sand:.0f} | {m_sand/m_c:.2f} |
| 石 | {m_stone:.0f} | {m_stone/m_c:.2f} |
| 水 | {m_w0:.0f} | {m_w0/m_c:.2f} |
| 外加剂 | {m_ad:.1f} | {m_ad/m_c:.4f} |
| **合计** | **{m_cp:.0f}** | |
| **水胶比** | **{wb_final:.3f}** | |
| **砂率** | **{sr:.1f}%** | |
""")

    st.session_state.baseline_params = {
        "水泥":cement_name,"f_ce":f_ce,"C₃A":cement_c3a,
        "砂来源":sand_source,"砂类型":sand_type,"砂FM":sand_fm,"含泥量":sand_mud,"MB":sand_mb,
        "石类型":stone_type,"Dmax":stone_dmax,
        "强度等级":strength_grade,"目标坍落度":target_slump,"施工方式":construction,
        "温度范围":f"{temp_min}~{temp_max}°C","W/B计算值":wb_final,"砂率":sr,
        "水泥用量":m_c,"砂用量":m_sand,"石用量":m_stone,"推荐掺量":rec_dosage
    }

# ═══════════════ PCE复配 ═══════════════
st.markdown("---")
st.markdown('<div id="pce"></div>', unsafe_allow_html=True)
st.header("🧬 PCE减水剂复配配方")
st.caption("按一吨(1000kg)出方")

with st.expander("🏭 外加剂厂家 & 场景", expanded=True):
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        factory_name = st.text_input("厂家", "自复配", key="pce_fac")
        scene = st.selectbox("场景", ["通用标准型","夏季高温","冬季低温","长距离保坍","自密实SCC","抗冻F300"], key="pce_sc")
    with col_f2:
        pce_solid_target = st.number_input("目标含固量 %", 10, 40, 20, 1, key="pce_st")
        pce_ret_target = st.number_input("保坍时间 h", 0.5, 4.0, 2.0, 0.5, key="pce_rt")

with st.expander("🧪 母液", expanded=True):
    with st.form("pce_ml_form"):
        mc1, mc2, mc3 = st.columns(3)
        with mc1:
            wr_name_p = st.text_input("减水母液", "PCE-WR-101", key="pwrn")
            wr_kg_p = st.number_input("减水母液用量 kg/吨", 0, 250, 130, 5, key="pwrk")
        with mc2:
            ret_name_p = st.text_input("保坍母液", "PCE-ST-201", key="prtn")
            ret_kg_p = st.number_input("保坍母液用量 kg/吨", 0, 200, 50, 5, key="prtk")
        with mc3:
            anti_name_p = st.text_input("抗泥母液(选配)", "PCE-AM-301", key="pann")
            anti_kg_p = st.number_input("抗泥母液用量 kg/吨", 0, 150, 0, 5, key="pank")
        st.form_submit_button("确认母液", type="primary")

with st.expander("🧩 小料种类 & 用量", expanded=True):
    # 缓凝剂
    st.caption("⏱️ 缓凝剂 — 葡钠为主(性价比高)·白糖为辅(改善和易性)·麦芽糊精补充(保水)")
    with st.form("pce_retarder_form"):
        rc1, rc2, rc3, rc4 = st.columns(4)
        with rc1:
            na_kg_p = st.number_input("葡钠 kg/吨", 0, 80, 20, 1, key="pnak")
        with rc2:
            sugar_kg_p = st.number_input("白糖 kg/吨", 0, 20, 3, 1, key="psuk")
        with rc3:
            malt_kg_p = st.number_input("麦芽糊精 kg/吨", 0, 30, 0, 1, key="pmak")
        with rc4:
            citric_kg_p = st.number_input("柠檬酸钠 kg/吨", 0, 15, 0, 1, key="pcik")
        st.form_submit_button("确认缓凝", type="primary")

    # 含气量
    st.caption("🫧 消泡剂 & 引气剂 — PCE自引气2~5%·先消后引精准控气")
    with st.form("pce_air_form"):
        ac1, ac2 = st.columns(2)
        with ac1:
            def_kg_p = st.number_input("消泡剂 kg/吨", 0.0, 3.0, 0.3, 0.1, key="pdefk")
            def_type_p = st.selectbox("消泡剂类型", ["聚醚改性有机硅","矿物油类","乳化硅油","聚醚类"], key="pdeft")
        with ac2:
            aea_kg_p = st.number_input("引气剂 kg/吨", 0.0, 5.0, 0.0, 0.1, key="paek")
            aea_type_p = st.selectbox("引气剂类型", ["不使用","AOS","松香皂","烷基苯磺酸盐","脂肪醇硫酸钠","皂角苷"], key="paet")
        st.form_submit_button("确认含气调节", type="primary")

    # 保水 + 防腐
    st.caption("🧴 保水增稠 & 🛡️ 防腐")
    with st.form("pce_vma_pres_form"):
        vc1, vc2 = st.columns(2)
        with vc1:
            vma_kg_p = st.number_input("VMA保水剂 kg/吨", 0.0, 10.0, 0.0, 0.5, key="pvmk")
            vma_type_p = st.selectbox("VMA类型", ["不使用","纤维素醚(HPMC)","聚丙烯酰胺(PAM)","温轮胶","黄原胶"], key="pvmt")
        with vc2:
            pres_kg_p = st.number_input("防腐剂 kg/吨", 0.0, 3.0, 1.5, 0.1, key="pprk")
            pres_type_p = st.selectbox("防腐剂类型", ["异噻唑啉酮(CIT/MIT)","苯甲酸钠","卡松","山梨酸钾"], key="pprt")
        st.form_submit_button("确认保水+防腐", type="primary")

# PCE汇总
pce_total_kg = wr_kg_p + ret_kg_p + anti_kg_p + na_kg_p + sugar_kg_p + malt_kg_p + citric_kg_p + def_kg_p + aea_kg_p + vma_kg_p + pres_kg_p
water_pce = 1000 - pce_total_kg
c1, c2 = st.columns(2)
c1.metric("💧 水", f"{water_pce:.0f} kg")
c1.metric("💧 水", f"{water_pce:.0f} kg")
c2.metric("💰 估算成本", f"¥{wr_kg_p*12+ret_kg_p*15+na_kg_p*8+sugar_kg_p*6+def_kg_p*60+pres_kg_p*15:.0f}/吨")

# PCE配方AI评价
if st.button("🤖 AI 评价PCE配方", key="pce_eval_btn"):
    kb = KNOWLEDGE_BASE[:3000] if KNOWLEDGE_BASE else ""
    eval_pce = f"""基于外加剂复配专业知识，评价以下PCE配方。

{chr(10).join(f'- {k}: {v}' for k,v in {
    "厂家":factory_name,"场景":scene,"目标含固量":pce_solid_target,"保坍时间":pce_ret_target,
    "减水母液":f"{wr_name_p} {wr_kg_p}kg/吨","保坍母液":f"{ret_name_p} {ret_kg_p}kg/吨",
    "抗泥母液":f"{anti_name_p} {anti_kg_p}kg/吨","葡钠":f"{na_kg_p}kg","白糖":f"{sugar_kg_p}kg",
    "麦芽糊精":f"{malt_kg_p}kg","柠檬酸钠":f"{citric_kg_p}kg",
    "消泡剂":f"{def_kg_p}kg({def_type_p})","引气剂":f"{aea_kg_p}kg({aea_type_p})",
    "VMA":f"{vma_kg_p}kg({vma_type_p})","防腐剂":f"{pres_kg_p}kg({pres_type_p})",
    "水量":f"{water_pce}kg","总量":f"{pce_total_kg+water_pce}kg"
}.items() if v)}

知识库参考: {kb[:2000]}

请给出:
1. **配方评分** (1-5)
2. **母液比例评价** (减水/保坍/抗泥比例是否合理)
3. **缓凝体系评价** (葡钠/白糖/麦芽糊精/柠檬酸钠的搭配)
4. **含气量控制** (消泡/引气方案)
5. **改进建议** (具体数字)"""
    resp = ds_client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role":"system","content":"你是PCE外加剂复配专家。基于知识库评价配方。"},
                  {"role":"user","content":eval_pce}],
        max_tokens=500
    )
    st.session_state.pce_eval = resp.choices[0].message.content

if "pce_eval" in st.session_state and st.session_state.pce_eval:
    st.markdown("---")
    st.markdown(st.session_state.pce_eval)

# ═══════════════ 独立产品 ═══════════════
st.markdown("---")
st.markdown('<div id="independent"></div>', unsafe_allow_html=True)
st.header("📦 独立外加剂产品")
st.caption("以下为独立产品线，不与PCE混配。仅在混凝土生产时单独添加。")

with st.expander("⚡ 早强剂", expanded=False):
    st.caption("加速早期强度。低温/预制构件/抢修。GB 8076 早强剂类。")
    acc_type = st.selectbox("类型", ["无氯复合型","硫酸钠基","硫氰酸钠基","甲酸钙基","纳米C-S-H晶种"], key="acc_t")
    acc_dosage = st.number_input("推荐掺量 %胶材", 0.5, 5.0, 2.0, 0.5, key="acc_d")
    st.success(f"{acc_type} · 掺量{acc_dosage}%")

with st.expander("❄️ 防冻剂", expanded=False):
    st.caption("降低冰点。冬季施工。JC 475。")
    af_type = st.selectbox("类型", ["无氯无碱复合型","亚硝酸钠基","硝酸钙基","尿素+乙二醇"], key="af_t")
    af_dosage = st.number_input("推荐掺量 %胶材", 1.0, 10.0, 3.0, 0.5, key="af_d")
    st.success(f"{af_type} · 掺量{af_dosage}%")

with st.expander("🚀 速凝剂", expanded=False):
    st.caption("喷射混凝土专用。GB/T 35159。")
    st.success("液体无碱速凝剂 · 掺量4-8%胶材 · 初凝≤5min")

with st.expander("📈 膨胀剂", expanded=False):
    st.caption("补偿收缩。GB/T 23439。")
    st.success("UEA/CSA型 · 掺量6-8%胶材")

with st.expander("🛡️ 阻锈剂", expanded=False):
    st.caption("海工/除冰盐环境。GB/T 31296。")
    st.success("亚硝酸钙 · 10-20L/m³ · 阳极钝化膜型")

# ═══════════════ 数据共享 ═══════════════
st.markdown("---")
st.markdown('<div id="share"></div>', unsafe_allow_html=True)
st.header("🌐 数据共享 & 协作")

s1, s2 = st.columns(2)
with s1:
    if st.button("📤 导出生产配比库"):
        if st.session_state.prod_mixes:
            st.download_button("下载 JSON", json.dumps(st.session_state.prod_mixes, ensure_ascii=False, indent=2), "production_mixes.json", "application/json")
with s2:
    uploaded = st.file_uploader("📥 导入合并", type=["json"])
    if uploaded and st.button("合并"):
        data = json.loads(uploaded.read())
        existing = {(m.get("日期",""), m.get("项目","")) for m in st.session_state.prod_mixes}
        added = 0
        for r in data:
            if (r.get("日期",""), r.get("项目","")) not in existing:
                with open(PROD_DB, "a", encoding="utf-8") as f:
                    f.write(json.dumps(r, ensure_ascii=False)+"\n")
                st.session_state.prod_mixes.append(r)
                existing.add((r.get("日期",""), r.get("项目","")))
                added += 1
        st.success(f"✅ 合并 {added} 条 (跳过 {len(data)-added} 条重复)")

st.markdown("---")
st.caption("🧪 知识引擎: JGJ 55-2011 · GB 50164 · GB 175 · GB/T 14684/14685 · GB/T 1596 · GB/T 18046 · GB 8076 · GB 50119")

# fmt: on
