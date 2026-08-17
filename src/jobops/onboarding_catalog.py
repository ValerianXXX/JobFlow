from __future__ import annotations

from typing import Any


STATUS_OPTIONS = ("UNKNOWN", "CONFIRMED", "PREFER_NOT_TO_ANSWER", "NOT_APPLICABLE")
USE_POLICIES = ("reuse", "confirm_each_application", "prefer_not_to_answer", "do_not_store")


def _field(
    field_id: str,
    group: str,
    zh: str,
    en: str,
    *,
    input_type: str = "text",
    help_zh: str = "",
    help_en: str = "",
    options: tuple[tuple[str, str, str], ...] = (),
    sensitive: bool = False,
    policy: str = "reuse",
    required_resolution: bool = True,
) -> dict[str, Any]:
    return {
        "id": field_id,
        "group": group,
        "label": {"zh": zh, "en": en},
        "help": {"zh": help_zh, "en": help_en},
        "input_type": input_type,
        "options": [
            {"value": value, "label": {"zh": option_zh, "en": option_en}}
            for value, option_zh, option_en in options
        ],
        "sensitive": sensitive,
        "default_policy": policy,
        "required_resolution": required_resolution,
    }


GROUPS = (
    {"id": "identity_and_contact", "label": {"zh": "身份与联系方式", "en": "Identity & contact"}},
    {"id": "job_target", "label": {"zh": "求职目标", "en": "Job target"}},
    {"id": "work_authorization_and_visa", "label": {"zh": "工作授权与签证", "en": "Work authorization & visa"}},
    {"id": "location_remote_relocation_travel", "label": {"zh": "地点、远程、搬迁与出差", "en": "Location, remote, relocation & travel"}},
    {"id": "compensation", "label": {"zh": "薪资", "en": "Compensation"}},
    {"id": "availability", "label": {"zh": "入职时间", "en": "Availability"}},
    {"id": "standard_application", "label": {"zh": "标准申请题", "en": "Standard application questions"}},
    {"id": "public_links", "label": {"zh": "公开链接与作品集", "en": "Public links & portfolio"}},
    {"id": "sensitive_or_legal", "label": {"zh": "敏感或法律题", "en": "Sensitive or legal questions"}},
    {"id": "voluntary_disclosure", "label": {"zh": "自愿披露", "en": "Voluntary disclosure"}},
)


YES_NO_UNKNOWN = (
    ("YES", "是", "Yes"), ("NO", "否", "No"), ("UNSURE", "不确定", "Unsure"),
)


FIELDS = (
    _field("first_name", "identity_and_contact", "名字", "First name", help_zh="优先从简历提取；只需确认一次。", help_en="Extracted from the resume when reliable; confirm once.", required_resolution=False),
    _field("last_name", "identity_and_contact", "姓氏", "Last name", help_zh="优先从简历提取；只需确认一次。", help_en="Extracted from the resume when reliable; confirm once.", required_resolution=False),
    _field("email", "identity_and_contact", "常用申请邮箱", "Primary application email", help_zh="加密保存，仅在你批准的申请中使用。", help_en="Encrypted locally and used only in applications you approve.", required_resolution=False),
    _field("phone", "identity_and_contact", "常用申请电话", "Primary application phone", help_zh="加密保存，仅在你批准的申请中使用。", help_en="Encrypted locally and used only in applications you approve.", required_resolution=False),
    _field("phone_type", "identity_and_contact", "电话类型", "Phone type", input_type="select", options=(("Mobile", "手机", "Mobile"), ("Home", "住宅", "Home"), ("Work", "工作", "Work"), ("Other", "其他", "Other")), required_resolution=False),
    _field("address", "identity_and_contact", "街道地址（可选）", "Street address (optional)", required_resolution=False),
    _field("city", "identity_and_contact", "城市（可选）", "City (optional)", required_resolution=False),
    _field("state", "identity_and_contact", "州、省或地区（可选）", "State, province, or region (optional)", required_resolution=False),
    _field("postal_code", "identity_and_contact", "邮政编码（可选）", "Postal code (optional)", required_resolution=False),
    _field("country", "identity_and_contact", "国家或地区（可选）", "Country or region (optional)", required_resolution=False),
    _field("target_roles", "job_target", "目标职位或方向", "Target roles or functions", input_type="tags", help_zh="可填写多个，用逗号分隔。", help_en="Enter multiple values separated by commas."),
    _field("target_industries", "job_target", "目标行业", "Target industries", input_type="tags"),
    _field("target_levels", "job_target", "目标级别", "Target levels", input_type="tags", options=(("intern", "实习", "Intern"), ("entry", "初级", "Entry"), ("mid", "中级", "Mid-level"), ("senior", "高级", "Senior"), ("lead", "负责人", "Lead"))),
    _field("work_authorization", "work_authorization_and_visa", "目标国家的工作授权", "Work authorization in target country", input_type="select", options=(("CONFIRMED", "已具备", "Authorized"), ("REQUIRES_SPONSORSHIP", "需要签证担保", "Requires sponsorship"), ("NOT_AUTHORIZED", "目前不具备", "Not currently authorized"), ("UNSURE", "不确定", "Unsure")), sensitive=True),
    _field("visa_sponsorship", "work_authorization_and_visa", "现在或未来是否需要签证担保", "Need visa sponsorship now or later", input_type="select", options=YES_NO_UNKNOWN, sensitive=True),
    _field("preferred_locations", "location_remote_relocation_travel", "期望工作地点", "Preferred work locations", input_type="tags"),
    _field("remote_preference", "location_remote_relocation_travel", "办公方式偏好", "Work arrangement preference", input_type="select", options=(("remote", "远程", "Remote"), ("hybrid", "混合", "Hybrid"), ("onsite", "现场", "On-site"), ("flexible", "均可", "Flexible"))),
    _field("relocation", "location_remote_relocation_travel", "是否愿意搬迁", "Willing to relocate", input_type="select", options=YES_NO_UNKNOWN, sensitive=True),
    _field("travel", "location_remote_relocation_travel", "是否接受出差", "Willing to travel", input_type="select", options=(("YES", "接受", "Yes"), ("LIMITED", "有限接受", "Limited"), ("NO", "不接受", "No"), ("UNSURE", "不确定", "Unsure")), sensitive=True),
    _field("minimum_salary", "compensation", "最低可接受薪资", "Minimum acceptable compensation", help_zh="请同时写明币种和周期；可选择不愿回答。", help_en="Include currency and period, or choose prefer not to answer.", sensitive=True),
    _field("desired_salary", "compensation", "期望薪资", "Desired compensation", help_zh="请同时写明币种和周期。", help_en="Include currency and period.", sensitive=True),
    _field("available_start_date", "availability", "最早可入职时间", "Earliest available start date", help_zh="可填写具体日期或通知期。", help_en="Enter a date or notice period.", sensitive=True),
    _field("why_company", "standard_application", "选择公司的主要原因", "Why this type of company", input_type="textarea", help_zh="填写通用动机，具体公司版本由系统按岗位生成。", help_en="Provide general motivation; JobOps will tailor it per company."),
    _field("why_role", "standard_application", "选择岗位的主要原因", "Why this type of role", input_type="textarea"),
    _field("referral_source", "standard_application", "常见申请来源", "Typical application source", input_type="select", options=(("OFFICIAL_CAREERS", "公司官网", "Company careers site"), ("REFERRAL", "推荐", "Referral"), ("RECRUITER", "招聘者", "Recruiter"), ("OTHER", "其他", "Other"))),
    _field("previous_employment", "standard_application", "是否曾在目标公司任职", "Previously employed by target company", input_type="select", options=YES_NO_UNKNOWN),
    _field("github_url", "public_links", "GitHub 公开链接（可选）", "Public GitHub URL (optional)", help_zh="仅在申请表提供对应字段时使用；每份审阅包都会显示绑定状态。", help_en="Used only when the form offers a matching field; each review packet shows its binding status.", required_resolution=False),
    _field("linkedin_url", "public_links", "LinkedIn 公开链接（可选）", "Public LinkedIn URL (optional)", help_zh="仅在申请表提供对应字段时使用。", help_en="Used only when the form offers a matching field.", required_resolution=False),
    _field("website_url", "public_links", "个人网站（可选）", "Personal website (optional)", help_zh="仅在申请表提供对应字段时使用。", help_en="Used only when the form offers a matching field.", required_resolution=False),
    _field("portfolio_url", "public_links", "作品集公开链接（可选）", "Public portfolio URL (optional)", help_zh="作品集文件可在资料区选择“作品集文件”安全上传。", help_en="Upload a portfolio file securely from Sources by choosing Portfolio file.", required_resolution=False),
    _field("background_check", "sensitive_or_legal", "是否愿意依法接受背景调查", "Consent to a lawful background check", input_type="select", options=YES_NO_UNKNOWN, sensitive=True, policy="confirm_each_application"),
    _field("non_compete", "sensitive_or_legal", "是否受竞业或其他限制", "Subject to non-compete or other restrictions", input_type="select", options=YES_NO_UNKNOWN, sensitive=True, policy="confirm_each_application"),
    _field("truthfulness_attestation", "sensitive_or_legal", "真实性声明处理方式", "Truthfulness attestation policy", input_type="select", options=(("CONFIRM_EACH", "每次申请前确认", "Confirm before every application"), ("DO_NOT_ACCEPT", "不自动接受", "Do not accept automatically")), sensitive=True, policy="confirm_each_application"),
    _field("electronic_signature", "sensitive_or_legal", "电子签名处理方式", "Electronic signature policy", input_type="select", options=(("CONFIRM_EACH", "每次申请前确认", "Confirm before every application"), ("DO_NOT_SIGN", "不由系统签署", "Never sign automatically")), sensitive=True, policy="confirm_each_application"),
    _field("race_ethnicity", "voluntary_disclosure", "种族或族裔", "Race or ethnicity", sensitive=True, policy="confirm_each_application"),
    _field("gender", "voluntary_disclosure", "性别", "Gender", sensitive=True, policy="confirm_each_application"),
    _field("disability", "voluntary_disclosure", "残障披露", "Disability disclosure", sensitive=True, policy="confirm_each_application"),
    _field("veteran_status", "voluntary_disclosure", "退伍军人身份", "Veteran status", sensitive=True, policy="confirm_each_application"),
    _field("religion", "voluntary_disclosure", "宗教披露", "Religious disclosure", sensitive=True, policy="confirm_each_application"),
)


FIELD_BY_ID = {str(item["id"]): item for item in FIELDS}
FIELD_IDS = tuple(FIELD_BY_ID)
REQUIRED_FIELD_IDS = tuple(field_id for field_id in FIELD_IDS if FIELD_BY_ID[field_id]["required_resolution"])


def empty_answers() -> dict[str, dict[str, Any]]:
    return {
        field_id: {
            "value": None,
            "status": "UNKNOWN",
            "source": "UNKNOWN",
            "use_policy": str(FIELD_BY_ID[field_id]["default_policy"]),
            "updated_at": None,
        }
        for field_id in FIELD_IDS
    }


def public_catalog() -> dict[str, Any]:
    return {
        "groups": [dict(item) for item in GROUPS],
        "fields": [dict(item) for item in FIELDS],
        "status_options": list(STATUS_OPTIONS),
        "use_policies": list(USE_POLICIES),
        "supported_locales": ["zh", "en"],
        "field_count": len(FIELDS),
        "required_field_count": len(REQUIRED_FIELD_IDS),
    }
