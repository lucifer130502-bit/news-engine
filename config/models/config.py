from pydantic import BaseModel
from typing import Dict, List, Optional


class ApiKeys(BaseModel):
    slack_webhook_url: str


class MinioConfig(BaseModel):
    endpoint: str
    access_key: str
    secret_key: str
    secure: bool
    bucket_pdfs: str
    bucket_results: str


class MySQLConfig(BaseModel):
    host: str
    port: int
    user: str
    password: str
    database: str


class ImageValidationConfig(BaseModel):
    """First-page-image validation. If the rendered page is blank/dark/useless,
    try the next page (up to max_pages). Pixel pre-filter catches obvious cases
    cheaply; LLM call only fires for ambiguous renders."""
    enabled: bool = True
    max_pages: int = 3
    use_llm: bool = True
    model: str = "google/gemini-2.5-flash"


class BackfillConfig(BaseModel):
    """One-shot backfill scripts gated by per-script flags.
    Default false everywhere — prevents accidental runs in shared envs."""
    enabled: bool = False                # ISIN backfill
    images_enabled: bool = False         # First-page-image generation backfill
    classification_enabled: bool = False  # Re-classify labels + headline backfill
    symbol_name_enabled: bool = False    # Normalise symbol -> unified symbol name backfill
    summary_enabled: bool = False        # Re-summarize rows stored as "Summary not available."
    summary_delete_unrecoverable: bool = False  # In the summary backfill, also DELETE rows with no PDF (can't be summarized)
    id_format_enabled: bool = False      # Convert existing hyphenated announcement ids to the hyphen-less 32-char form


class QuoteMysqlConfig(BaseModel):
    """Quote DB (samco internal). Read-only; used by symbol_master to resolve ISIN
    from NSE_QUOTE / BSE_QUOTE tables instead of the public NSE/BSE web endpoints.
    Disabled by default — the engine falls back to public APIs when disabled or
    unreachable."""
    enabled: bool = False
    host: str = ""
    port: int = 3306
    user: str = ""
    password: str = ""
    database: str = "quote_data"


class AppConfig(BaseModel):
    crawl_interval_seconds: int
    llm_model: str


class LogConfig(BaseModel):
    dir: str
    file: str


class LLMGatewayConfig(BaseModel):
    enabled: bool
    base_url: str
    api_key: str


class SebiEmailConfig(BaseModel):
    transport: str = "smtp"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_use_tls: bool = True
    from_addr: str = "sebi-alerts@samco.in"
    mode: str = "per-item"  # per-item | daily-digest
    fallback_recipients: List[str] = []


class SebiMinioConfig(BaseModel):
    pdf_bucket: str = "sebi-circulars-pdfs"
    result_bucket: str = "sebi-circulars-results"


class SebiSlackConfig(BaseModel):
    webhook_url: str = ""
    fallback_channel: str = ""


class SebiLLMConfig(BaseModel):
    model: str = ""  # empty -> fall back to app.llm_model
    prompt_key: str = "sebi_compliance_v1"


class SebiDeliveryConfig(BaseModel):
    max_retries: int = 3
    backoff_seconds: List[int] = [30, 120, 600]


class SebiConfig(BaseModel):
    enabled: bool = False
    interval_minutes: int = 45
    list_base_url: str = "https://www.sebi.gov.in/sebiweb/home/HomeAction.do"
    sources: dict = {"circulars": True, "master_circulars": True}
    departments_allowlist: List[str] = []
    subsections_allowlist: List[str] = []
    fallback_admin_emails: List[str] = []
    minio: SebiMinioConfig = SebiMinioConfig()
    llm: SebiLLMConfig = SebiLLMConfig()
    slack: SebiSlackConfig = SebiSlackConfig()
    email: SebiEmailConfig = SebiEmailConfig()
    delivery: SebiDeliveryConfig = SebiDeliveryConfig()


class NotificationFilters(BaseModel):
    """STOCKNOTES_FILTER_MASTER ids for the six AI-news subscription dimensions.

    Required for audience resolution by get_user_list_for_ai_news: a user must be
    subscribed to ALL SIX to receive the news (HAVING COUNT(DISTINCT fid)=6).
    Confirm these ids with the team for the AI-news source.
    """
    media_id: str = ""
    source_id: str = ""
    segment_id: str = ""
    sentiment_id: str = ""
    asset_id: str = ""
    event_id: str = ""


class EventLabelRule(BaseModel):
    """Maps a classification-label substring to an Event filter id (first match wins)."""
    match: str
    id: str


class NotificationFilterMap(BaseModel):
    """STOCKNOTES_FILTER_MASTER id mappings used to DERIVE the six dimension ids per
    announcement (see STOCKNOTES_FILTER_MASTER.csv). The ids live in yaml, not code, so
    they can be retuned without a code change. Defaults mirror the current master."""
    source_by_exchange: Dict[str, str] = {"NSE": "44", "BSE": "45"}
    sentiment: Dict[str, str] = {"POSITIVE": "10", "NEUTRAL": "9", "NEGATIVE": "11"}
    sentiment_default: str = "9"          # Neutral
    segment: str = "21"                   # Equity
    asset: str = "17"                     # Equity and Equity Derivatives
    media_with: str = "26"
    media_without: str = "27"
    event_default: str = "30"             # Updates and Clarifications
    event_by_label: List[EventLabelRule] = []


class NotificationConfig(BaseModel):
    """Push each processed announcement into the notification inbox so the
    notification-service Feeds (fds) card fields populate from inbox.extras.

    Delivery goes through stocknote-notification's
    POST /Notification/SendAINews/1.0.0, which resolves the audience via the
    get_user_list_for_ai_news stored procedure and writes the inbox document
    carrying our card fields in `extra`.
    """
    enabled: bool = False
    # Base host of stocknote-notification (the /Notification/SendAINews path is appended).
    notification_base_url: str = ""
    # Discover/news API + CDN base used to build the card image / pdf URLs
    # (e.g. https://beta-discoverws.sam-co.in). Empty -> falls back to pdf_url.
    news_base_url: str = ""
    # Customer-app base that serves /news/... for the public share link
    # (e.g. https://app.samco.in). Empty -> falls back to news_base_url.
    share_base: str = ""
    # Audience tier: GUEST | SAMCO | H | HM | HML | M | L | HL | ML | WF.
    # For SAMCO/GUEST the six filter ids drive the audience; the symbol-form
    # only matters for the watchlist/holdings tiers.
    user_type: str = "SAMCO"
    # extra.SCREEN value carried into inbox.extras for app deep-linking.
    screen: str = "STOCK_NOTE"
    # Card category label. For "ai news" notification-service derives the displayed
    # `typ` from this (falls back to "Market Insights"). The per-item badges go in
    # classificationLabels, not here.
    category: str = "Market Insights"
    # Per-dimension FORCE-OVERRIDE ids (blank = use the derived value from filter_map).
    filters: NotificationFilters = NotificationFilters()
    # STOCKNOTES_FILTER_MASTER id mappings used to derive the six ids per announcement.
    filter_map: NotificationFilterMap = NotificationFilterMap()
    timeout_seconds: int = 10


class Config(BaseModel):
    app_profile: str
    api_keys: ApiKeys
    mysql: MySQLConfig
    app: AppConfig
    log: LogConfig
    llm_gateway: LLMGatewayConfig | None = None
    minio: Optional[MinioConfig] = None
    sebi: Optional[SebiConfig] = None
    quote_mysql: Optional[QuoteMysqlConfig] = None
    backfill: Optional[BackfillConfig] = None
    image_validation: Optional[ImageValidationConfig] = ImageValidationConfig()
    notification: Optional[NotificationConfig] = NotificationConfig()
