import os
import json
import pandas as pd
import requests
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI
import streamlit as st

st.set_page_config(
    page_title="AI 商业线索捕获与分流系统",
    page_icon="⚡",
    layout="wide"
)
# 兼容读取：优先读 Streamlit Cloud 的 Secrets，读不到再读本地 .env
def get_val(key, default=""):
    if key in st.secrets:
        return st.secrets[key]
    return os.getenv(key, default)

LLM_API_KEY = get_val("LLM_API_KEY")
LLM_BASE_URL = get_val("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = get_val("LLM_MODEL", "deepseek-chat")
WECOM_WEBHOOK = get_val("WECOM_WEBHOOK_URL", "")

client = OpenAI(
    api_key=LLM_API_KEY,
    base_url=LLM_BASE_URL
)

EXCEL_FILE = "leads_database.xlsx"

def extract_lead_info(raw_message: str) -> dict:
    prompt = f"""
你是一个资深销售线索分析专家。请分析客户的留言或对话记录，严格提取关键信息并返回 JSON。
JSON 字段必须且仅包含：
- name: 客户称谓（若无则填 "客户"）
- contact: 联系方式（提取手机号、微信号、邮箱或社媒账号；若未提供填 "未提供"）
- industry: 客户行业领域（如 "跨境电商"、"汽配五金"、"个人买家" 等）
- requirement: 核心诉求摘要（简明扼要，25字以内）
- intent_level: 意向等级（仅填 A 或 B 或 C。A: 极高/有明确预算/急迫; B: 中等/常规询价; C: 泛咨询/无明确需求）
- draft_reply: 针对该客户痛点的首次专业跟进话术（语气专业友好，引导下一步沟通）

客户留言原文：
\"\"\"{raw_message}\"\"\"
"""
    response = client.chat.completions.create(
        model=os.getenv("LLM_MODEL", "deepseek-chat"),
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.2
    )
    return json.loads(response.choices[0].message.content)

def save_to_excel(lead_data: dict):
    """带严格数据类型清洗的持久化存储，防止 PyArrow 渲染冲突"""
    record = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "name": str(lead_data.get("name", "客户")),
        "contact": str(lead_data.get("contact", "未提供")),
        "industry": str(lead_data.get("industry", "未知")),
        "intent_level": str(lead_data.get("intent_level", "C")),
        "requirement": str(lead_data.get("requirement", "")),
        "draft_reply": str(lead_data.get("draft_reply", ""))
    }
    
    new_df = pd.DataFrame([record])
    
    if os.path.exists(EXCEL_FILE):
        existing_df = pd.read_excel(EXCEL_FILE, dtype=str)
        updated_df = pd.concat([existing_df, new_df], ignore_index=True)
        updated_df.to_excel(EXCEL_FILE, index=False)
    else:
        new_df.to_excel(EXCEL_FILE, index=False)

def send_wecom_notification(lead_data: dict) -> tuple[bool, str]:
    """优化版企业微信高优先级推送排版"""
    if not WECOM_WEBHOOK:
        return False, "未配置 WECOM_WEBHOOK_URL"

    level = lead_data.get("intent_level", "C")
    level_badge = "🔥【S级核心大单】" if level == "A" else "⚡【常规商机】"
    name_color = "warning" if level == "A" else "comment"

    # 企业微信原生 Markdown 样式优化
    content = f"""### {level_badge} 新线索到达
> **客户信息**：<font color="{name_color}">{lead_data.get('name')}</font> ({level}级商机)
> **联系方式**：<font color="info">{lead_data.get('contact')}</font>
> **所属行业**：{lead_data.get('industry')}
> **核心需求**：{lead_data.get('requirement')}
> 
> **建议首次跟进话术**：
> <font color="comment">{lead_data.get('draft_reply')}</font>
> 
> *触发系统：AI Lead Agent 自动捕获引擎*
"""
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "content": content
        }
    }
    
    try:
        res = requests.post(WECOM_WEBHOOK, json=payload, timeout=5)
        res_json = res.json()
        if res_json.get("errcode") == 0:
            return True, "企微实时告警已发送"
        return False, f"企微接口报错: {res_json.get('errmsg')}"
    except Exception as e:
        return False, f"网络请求超时或错误: {e}"

# --- 前端界面 ---
st.title("⚡ 商业线索智能捕获与分流 Agent")
st.caption("实时清洗自然语言线索、评估商机等级、一键入库并秒级触达企业微信群。")

# 侧边栏配置与监控
with st.sidebar:
    st.header("⚙️ 引擎运行配置")
    wecom_status = "🟢 已连接" if WECOM_WEBHOOK else "🔴 未绑定"
    st.markdown(f"**企业微信通道：** {wecom_status}")
    
    push_rule = st.selectbox(
        "推送触发规则：",
        options=["全部意向等级均推送", "仅推送 A 级 (高意向/大单)", "仅推送 A/B 级 (过滤泛咨询)"],
        index=2
    )
    
    st.divider()
    st.markdown("**数据维护：**")
    if os.path.exists(EXCEL_FILE) and st.button("🗑️ 清空历史测试数据"):
        os.remove(EXCEL_FILE)
        st.success("历史表格已清除，重新写入将生成新表！")
        st.rerun()

col_input, col_result = st.columns([1, 1])

with col_input:
    st.subheader("📥 录入客户留言 / 聊天记录")
    sample_text = "刘总好，我们是做五金工具外贸的，想采购你们这套针对海外客户的自动跟单系统，下半年预算在2-3万左右，这周方便电话沟通吗？手机/微信：13812345678"
    raw_message = st.text_area("粘贴客户咨询原文：", value=sample_text, height=180)
    trigger_btn = st.button("🚀 启动智能分析与分发", type="primary", use_container_width=True)

with col_result:
    st.subheader("📋 结构化捕获与推送反馈")
    if trigger_btn:
        if not raw_message.strip():
            st.warning("咨询内容不能为空！")
        else:
            with st.spinner("Agent 正在深度解析意图、评级并入库..."):
                try:
                    lead = extract_lead_info(raw_message)
                    save_to_excel(lead)
                    
                    # 判断推送逻辑
                    level = lead.get("intent_level")
                    should_push = True
                    if push_rule == "仅推送 A 级 (高意向/大单)" and level != "A":
                        should_push = False
                    elif push_rule == "仅推送 A/B 级 (过滤泛咨询)" and level not in ["A", "B"]:
                        should_push = False
                    
                    push_msg = "🔕 根据规则，当前等级未触发微信推送"
                    if should_push:
                        ok, push_msg = send_wecom_notification(lead)
                    
                    st.success("✅ 数据已持久化存储至 Excel！")
                    if should_push:
                        st.info(f"🔔 {push_msg}")
                    else:
                        st.caption(push_msg)
                    
                    # 关键信息看板
                    kpi1, kpi2, kpi3 = st.columns(3)
                    kpi1.metric("意向等级", f"{level} 级")
                    kpi2.metric("客户称谓", lead.get("name"))
                    kpi3.metric("联系方式", lead.get("contact"))
                    
                    st.markdown(f"**目标行业：** `{lead.get('industry')}`")
                    st.markdown(f"**核心诉求：** {lead.get('requirement')}")
                    st.info(f"💡 **一键复制跟进话术：**\n\n{lead.get('draft_reply')}")
                    
                except Exception as e:
                    st.error(f"处理失败，原因: {e}")

st.divider()

# 数据表与过滤导出
st.subheader("📊 实时商机库总览")
if os.path.exists(EXCEL_FILE):
    data_df = pd.read_excel(EXCEL_FILE, dtype=str)
    
    f1, f2 = st.columns([1, 2])
    with f1:
        levels = st.multiselect("按商机评级筛选", ["A", "B", "C"], default=["A", "B", "C"])
    with f2:
        search_kw = st.text_input("全局关键词搜索 (姓名/行业/联系方式)：", "")
    
    view_df = data_df[data_df["intent_level"].isin(levels)]
    if search_kw:
        view_df = view_df[
            view_df["name"].str.contains(search_kw, na=False) |
            view_df["industry"].str.contains(search_kw, na=False) |
            view_df["contact"].str.contains(search_kw, na=False)
        ]
        
    st.dataframe(view_df, use_container_width=True)
    
    with open(EXCEL_FILE, "rb") as f:
        st.download_button(
            label="📥 导出完整商机 Excel 表格",
            data=f,
            file_name="客户商机线索汇总.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
else:
    st.info("当前暂无线索记录，请在上方执行测试。")