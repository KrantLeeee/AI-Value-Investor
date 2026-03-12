"""Industry classification and representative stock mapping.

Uses AKShare APIs verified on 2026-03-11:
- stock_industry_change_cninfo: Stock → Industry mapping
- stock_individual_info_em: Stock info including industry
"""

import akshare as ak

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Manually curated representative stocks for major industries
# Format: {industry_name: [(ticker, name), ...]}
# Industry aliases: map various names to canonical industry names
INDUSTRY_ALIASES = {
    "信息技术服务": "数据中心",
    "软件和信息技术服务业": "数据中心",
    "计算机服务": "数据中心",
    "IDC": "数据中心",
    "互联网数据中心": "数据中心",
    "通信服务": "数据中心",
    "化学制剂": "医药",
    "化学制药": "医药",
    "生物制品": "医药",
    "医疗器械": "医药",
    "中药": "医药",
    "电力/公用事业": "电力",
    "水电": "电力",
    "火电": "电力",
    "核电": "电力",
}

REAL_ESTATE_CONFIG = {
    'pb_multiple_cap': 0.5,
    'warning_text': '⚠️ 房地产开发行业不适合使用标准P/B倍数估值。'
                    '当前估值仅供参考，建议参考NAV（净资产价值折价法）。',
    'disable_methods': ['graham_number'],
}

INDUSTRY_REPRESENTATIVES = {
    "数据中心": [
        ("603881", "数据港"),
        ("300383", "光环新网"),
        ("300166", "东方国信"),
        ("600804", "鹏博士"),
        ("300299", "富春股份"),
        ("002212", "天融信"),
        ("300454", "深信服"),
    ],
    "银行": [
        ("601398", "工商银行"),
        ("601939", "建设银行"),
        ("601288", "农业银行"),
        ("601988", "中国银行"),
        ("600036", "招商银行"),
        ("000001", "平安银行"),
        ("601166", "兴业银行"),
        ("600000", "浦发银行"),
        ("601818", "光大银行"),
        ("002142", "宁波银行"),
    ],
    "白酒": [
        ("600519", "贵州茅台"),
        ("000858", "五粮液"),
        ("000568", "泸州老窖"),
        ("002304", "洋河股份"),
        ("000596", "古井贡酒"),
        ("600779", "水井坊"),
        ("603369", "今世缘"),
        ("600559", "老白干酒"),
    ],
    "保险": [
        ("601318", "中国平安"),
        ("601628", "中国人寿"),
        ("601336", "新华保险"),
        ("601601", "中国太保"),
        ("601319", "中国人保"),
    ],
    "房地产": [
        ("000002", "万科A"),
        ("001979", "招商蛇口"),
        ("600048", "保利发展"),
        ("600383", "金地集团"),
        ("000069", "华侨城A"),
    ],
    "医药": [
        ("600276", "恒瑞医药"),
        ("000538", "云南白药"),
        ("600196", "复星医药"),
        ("002007", "华兰生物"),
        ("300760", "迈瑞医疗"),
        ("603259", "药明康德"),
        ("000963", "华东医药"),
        ("002001", "新和成"),
    ],
    "电力": [
        ("600900", "长江电力"),
        ("601991", "大唐发电"),
        ("600886", "国投电力"),
        ("000027", "深圳能源"),
        ("600025", "华能水电"),
        ("003816", "中国广核"),
        ("601985", "中国核电"),
    ],
    "互联网": [
        ("300059", "东方财富"),
        ("002230", "科大讯飞"),
        ("002415", "海康威视"),
        ("002475", "立讯精密"),
        ("603881", "数据港"),
        ("300496", "中科创达"),
    ],
    "新能源": [
        ("300750", "宁德时代"),
        ("002594", "比亚迪"),
        ("601012", "隆基绿能"),
        ("002129", "TCL中环"),
        ("300274", "阳光电源"),
    ],
    "家电": [
        ("000651", "格力电器"),
        ("000333", "美的集团"),
        ("600690", "海尔智家"),
        ("002508", "老板电器"),
        ("002242", "九阳股份"),
    ],
    "食品饮料": [
        ("600887", "伊利股份"),
        ("002714", "牧原股份"),
        ("603288", "海天味业"),
        ("002568", "百润股份"),
        ("600597", "光明乳业"),
    ],
}


def get_stock_industry(ticker: str) -> str | None:
    """
    Get industry classification for a stock.

    Uses CNINFO classification via AKShare.
    """
    # Clean ticker (remove .SH/.SZ suffix if present)
    clean_ticker = ticker.split(".")[0]

    try:
        df = ak.stock_industry_change_cninfo(symbol=clean_ticker)
        if df is not None and not df.empty:
            # Use 巨潮分类 if available
            juchao_row = df[df["分类标准"] == "巨潮行业分类标准"]
            if not juchao_row.empty:
                return juchao_row.iloc[0]["行业大类"]
            # Fallback to first row
            return df.iloc[0]["行业大类"]
    except Exception as e:
        logger.warning("Failed to get industry for %s via CNINFO: %s", ticker, e)

    # Fallback: check if in representative stocks
    for industry, stocks in INDUSTRY_REPRESENTATIVES.items():
        for stock_ticker, _ in stocks:
            if stock_ticker == clean_ticker:
                return industry

    return None


def get_industry_representatives(industry: str) -> list[dict]:
    """
    Get representative stocks for an industry.

    Returns:
        List of {ticker, name} dicts
    """
    # Resolve alias first
    canonical_industry = INDUSTRY_ALIASES.get(industry, industry)

    # Try exact match first
    if canonical_industry in INDUSTRY_REPRESENTATIVES:
        return [
            {"ticker": t, "name": n}
            for t, n in INDUSTRY_REPRESENTATIVES[canonical_industry]
        ]

    # Try partial match
    for ind_name, stocks in INDUSTRY_REPRESENTATIVES.items():
        if ind_name in canonical_industry or canonical_industry in ind_name:
            return [
                {"ticker": t, "name": n}
                for t, n in stocks
            ]

    logger.warning("No representatives found for industry: %s (canonical: %s)", industry, canonical_industry)
    return []


def find_industry_for_stock(ticker: str) -> str | None:
    """
    Find industry for a stock, trying multiple methods.

    Returns:
        Canonical industry name or None
    """
    # Method 1: Check representative stocks first (most reliable)
    clean_ticker = ticker.split(".")[0]
    for ind_name, stocks in INDUSTRY_REPRESENTATIVES.items():
        for stock_ticker, _ in stocks:
            if stock_ticker == clean_ticker:
                return ind_name

    # Method 2: Try CNINFO API
    industry = get_stock_industry(ticker)
    if industry:
        # Resolve to canonical name
        return INDUSTRY_ALIASES.get(industry, industry)

    return None


# New industry type mappings (v2.1.0)
NEW_INDUSTRY_MAPPINGS = {
    # Cyclical Materials
    '有色金属': 'cyclical_materials',
    '钢铁': 'cyclical_materials',
    '水泥': 'cyclical_materials',
    '化工': 'cyclical_materials',
    '铝业': 'cyclical_materials',
    '铜业': 'cyclical_materials',

    # Telecom
    '通信运营': 'telecom_operator',
    '电信运营': 'telecom_operator',
    '通信设备': 'telecom_equipment',
    '网络设备': 'telecom_equipment',

    # New Energy
    '新能源汽车': 'auto_new_energy',
    '电动汽车': 'auto_new_energy',
    '锂电池': 'new_energy_mfg',
    '动力电池': 'new_energy_mfg',
    '储能': 'new_energy_mfg',

    # Agriculture
    '养殖': 'cyclical_agri',
    '生猪养殖': 'cyclical_agri',
    '畜牧': 'cyclical_agri',

    # Defense
    '军工': 'defense_equipment',
    '国防': 'defense_equipment',
    '航空航天': 'defense_equipment',

    # Low Margin Manufacturing
    '电子制造': 'low_margin_mfg',
    'ODM': 'low_margin_mfg',
    'OEM': 'low_margin_mfg',
    '代工': 'low_margin_mfg',
}


def get_industry_type(company_name: str, sector: str) -> str:
    """
    Get industry type from company name and sector.

    Args:
        company_name: Company name
        sector: Sector classification from data source

    Returns:
        Industry type string (matches keys in industry_profiles.yaml)
    """
    # Check direct sector mapping first
    for keyword, industry_type in NEW_INDUSTRY_MAPPINGS.items():
        if keyword in sector:
            return industry_type

    # Check company name for keywords
    for keyword, industry_type in NEW_INDUSTRY_MAPPINGS.items():
        if keyword in company_name:
            return industry_type

    return 'generic'
