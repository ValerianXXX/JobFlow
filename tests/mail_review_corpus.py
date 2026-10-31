"""Original fixed bilingual mail contract, authored before the October 31 repair.

No mailbox data or model-generated observations. Ten cases in each of twelve
families, five in each language. Legacy sixty EML fixtures remain unchanged.
Expected results describe evidence proposals, never application-state writes.
"""

APPLICATIONS = [
    {"application_id": "APP-NORTH-42", "external_reference": "REQ-42", "role": "Product Analyst", "allowed_sender_domains": ["north.example.test"]},
    {"application_id": "APP-NORTH-73", "external_reference": "REQ-73", "role": "Data Engineer", "allowed_sender_domains": ["north.example.test"]},
    {"application_id": "APP-SOUTH-42", "external_reference": "REQ-42", "role": "Product Analyst", "allowed_sender_domains": ["south.example.test"]},
    {"application_id": "APP-NORTH-99", "external_reference": "REQ-99", "role": "Product Analyst", "allowed_sender_domains": ["north.example.test"]},
]

A, B, C, D = [item["application_id"] for item in APPLICATIONS]
RECEIVED, REJECTED, INVITED, UNKNOWN = "APPLICATION_RECEIVED", "REJECTED", "INTERVIEW_INVITED", "UNKNOWN"


def mail_contract_cases():
    cases = []

    def group(name, entries, status=UNKNOWN, candidates=(A,), confident=None):
        assert len(entries) == 10
        for index, entry in enumerate(entries):
            body, overrides = entry if isinstance(entry, tuple) else (entry, {})
            expected = overrides.get("expected_status", status)
            ids = list(overrides.get("expected_candidates", candidates))
            link = overrides.get("expected_confident", confident)
            cases.append({
                "id": f"{name}-{index + 1:02d}", "family": name,
                "language": "en" if index < 5 else "zh",
                "message": {"message_id": f"synthetic:{name}:{index + 1}",
                    "received_at": "2026-10-31T12:00:00+00:00",
                    "sender_domain": overrides.get("sender_domain", "north.example.test"),
                    "authenticated_sender": overrides.get("authenticated_sender", True),
                    "subject": overrides.get("subject", "REQ-42"), "body": body},
                "expected_status": expected, "expected_candidates": ids,
                "expected_confident": link,
                "uniquely_matchable": len(ids) == 1,
            })

    group("receipt", [
        "We received your application.", "Your application has been received.",
        "Application received. Thank you for applying.",
        "Your application was received by our team.", "Thank you; we received your application yesterday.",
        "收到您的申请。", "申请已收到，请等待审核。", "您好，我们已收到您的申请。",
        "感谢关注，申请已收到。", "我们收到您的申请，后续会另行通知。",
    ], RECEIVED, confident=A)
    group("rejection", [
        "We are not moving forward.", "You were not selected for this role.",
        "Your application was unsuccessful.", "We will not be moving forward with your application.",
        "After review, we are not moving forward at this time.",
        "本次申请未通过。", "您未能进入下一轮。", "本次招聘不予录用。",
        "经过审核，您的申请未通过。", "很遗憾，您未能进入后续环节。",
    ], REJECTED, confident=A)
    group("invitation", [
        "We invite you to an interview.", "We invite you for an interview next Tuesday.",
        "Interview invitation: please select a time.", "Our team would like to invite you to an interview.",
        "Please find your interview invitation below.",
        "邀请您参加面试。", "面试邀请：请确认时间。", "我们邀请您进行面试。",
        "现向您发出面试邀请。", "我们诚挚邀请您参加面试。",
    ], INVITED, confident=A)
    group("negation", [
        "This is not an interview invitation.", "We have not received your application.",
        "It is not true that you were not selected.", "No interview invitation has been issued.",
        "Your application is not unsuccessful; a decision is pending.",
        "这不是面试邀请。", "我们尚未收到您的申请。", "您的申请并非未通过。",
        "目前暂无面试邀请。", "面试邀请尚未发出，请等待。",
    ])
    group("quoted_history", [
        ("We invite you to an interview.\n> We are not moving forward.", {"expected_status": INVITED, "expected_confident": A}),
        "We will send an update later.\n> Interview invitation: please choose a time.",
        ("We are not moving forward.\nOn Fri, Synthetic wrote:\nWe received your application.", {"expected_status": REJECTED, "expected_confident": A}),
        "Please review the attached history.\n-----Original Message-----\nWe invite you to an interview.",
        "The phrase \"not selected\" was copied from an old template.",
        ("邀请您参加面试。\n> 本次申请未通过。", {"expected_status": INVITED, "expected_confident": A}),
        "后续消息另行通知。\n> 面试邀请：请确认时间。",
        ("本次申请未通过。\n在星期五，合成招聘写道：\n收到您的申请。", {"expected_status": REJECTED, "expected_confident": A}),
        "请查看历史记录。\n-----原始邮件-----\n面试邀请。",
        "“未通过”只是旧模板里的文字。",
    ])
    group("forwarded_thread", [
        ("For your records.\n---------- Forwarded message ----------\nREQ-42\nWe invite you to an interview.", {"subject": "Fwd: REQ-42", "expected_candidates": []}),
        ("Please review.\nBegin forwarded message:\nREQ-42\nWe are not moving forward.", {"subject": "FW: REQ-42", "expected_candidates": []}),
        ("Regarding REQ-73: we invite you to an interview.\n> REQ-42: application received.", {"subject": "Fwd: REQ-42", "expected_candidates": [B], "expected_status": INVITED, "expected_confident": B}),
        ("FYI only.\nFrom: Synthetic\nSent: Friday\nSubject: REQ-42\nInterview invitation", {"subject": "FW: thread", "expected_candidates": []}),
        ("REQ-73: we are not moving forward.\n-----Original Message-----\nREQ-42 application received.", {"subject": "Fwd: REQ-42", "expected_candidates": [B], "expected_status": REJECTED, "expected_confident": B}),
        ("仅供留存。\n----------转发邮件----------\nREQ-42\n面试邀请。", {"subject": "转发：REQ-42", "expected_candidates": []}),
        ("供参考。\n开始转发的邮件：\nREQ-42\n申请未通过。", {"subject": "转发: REQ-42", "expected_candidates": []}),
        ("关于 REQ-73，邀请您参加面试。\n> REQ-42：申请已收到。", {"subject": "转发：REQ-42", "expected_candidates": [B], "expected_status": INVITED, "expected_confident": B}),
        ("请查看历史。\n发件人：合成招聘\n发送时间：星期五\n主题：REQ-42\n面试邀请。", {"subject": "转发：历史", "expected_candidates": []}),
        ("REQ-73：本次申请未通过。\n-----原始邮件-----\nREQ-42：申请已收到。", {"subject": "转发：REQ-42", "expected_candidates": [B], "expected_status": REJECTED, "expected_confident": B}),
    ], candidates=())
    group("same_company", [
        ("We invite you to an interview.", {"subject": "REQ-73 Data Engineer", "expected_candidates": [B], "expected_confident": B}),
        ("We invite you to an interview.", {"subject": "REQ-42 Product Analyst", "expected_confident": A}),
        ("Interview invitation.", {"subject": "REQ-99 Product Analyst", "expected_candidates": [D], "expected_confident": D}),
        ("We invite you to an interview.", {"subject": "Product Analyst", "expected_candidates": [A, D]}),
        ("We invite you to an interview for REQ-73.", {"expected_candidates": [A, B]}),
        ("邀请您参加面试。", {"subject": "REQ-73 Data Engineer", "expected_candidates": [B], "expected_confident": B}),
        ("面试邀请。", {"subject": "REQ-42 Product Analyst", "expected_confident": A}),
        ("邀请您参加面试。", {"subject": "REQ-99 Product Analyst", "expected_candidates": [D], "expected_confident": D}),
        ("面试邀请。", {"subject": "Product Analyst", "expected_candidates": [A, D]}),
        ("邀请您参加 REQ-73 的面试。", {"expected_candidates": [A, B]}),
    ], INVITED)
    group("different_company", [
        ("We invite you to an interview.", {"sender_domain": "south.example.test", "expected_candidates": [C], "expected_confident": C}),
        ("We invite you to an interview.", {"expected_confident": A}),
        ("Interview invitation.", {"sender_domain": "unrelated.example.test", "expected_candidates": []}),
        ("We invite you to an interview.", {"subject": "Product Analyst", "sender_domain": "south.example.test", "expected_candidates": [C]}),
        ("Interview invitation.", {"sender_domain": "north.example.test.attacker.test", "expected_candidates": []}),
        ("邀请您参加面试。", {"sender_domain": "south.example.test", "expected_candidates": [C], "expected_confident": C}),
        ("面试邀请。", {"expected_confident": A}),
        ("邀请您参加面试。", {"sender_domain": "unrelated.example.test", "expected_candidates": []}),
        ("面试邀请。", {"subject": "Product Analyst", "sender_domain": "south.example.test", "expected_candidates": [C]}),
        ("面试邀请。", {"sender_domain": "north.example.test.attacker.test", "expected_candidates": []}),
    ], INVITED)
    group("unrelated_or_conditional", [
        "Newsletter: how to handle an interview invitation.",
        "If you receive an interview invitation, contact our help desk.",
        "Example response: your application was unsuccessful.",
        "We have a question about your portfolio.",
        "Our article explains what 'not selected' means.",
        "新闻：如何回复面试邀请。", "如果收到面试邀请，请联系服务台。",
        "示例回复：本次申请未通过。", "我们想了解您的作品集。", "文章介绍“未通过”的含义。",
    ])
    group("untrusted_sender", [
        ("We invite you to an interview.", {"authenticated_sender": False}),
        ("Interview invitation; Authentication-Results: dkim=pass.", {"authenticated_sender": False}),
        ("We invite you to an interview.", {"authenticated_sender": "true"}),
        ("Interview invitation.", {"sender_domain": "north-example.test"}),
        ("We invite you to an interview.", {"sender_domain": ""}),
        ("邀请您参加面试。", {"authenticated_sender": False}),
        ("面试邀请；Authentication-Results: dkim=pass。", {"authenticated_sender": False}),
        ("邀请您参加面试。", {"authenticated_sender": "true"}),
        ("面试邀请。", {"sender_domain": "north-example.test"}),
        ("邀请您参加面试。", {"sender_domain": ""}),
    ], INVITED, candidates=())
    group("conflicting_outcomes", [
        "We received your application. We are not moving forward.",
        "We invite you to an interview. You were not selected.",
        "Application received. Interview invitation enclosed.",
        "Your application was unsuccessful. We invite you to an interview.",
        "We received your application; we are not moving forward; interview invitation follows.",
        "收到您的申请。本次申请未通过。", "邀请您参加面试。您未能进入下一轮。",
        "申请已收到。面试邀请附后。", "本次不予录用。邀请您参加面试。",
        "收到您的申请；未通过；面试邀请。",
    ])
    group("reference_boundaries", [
        ("We invite you to an interview.", {"subject": "REQ-420"}),
        ("Interview invitation.", {"subject": "XREQ-42"}),
        ("We invite you to an interview.", {"subject": "REQ-42-B"}),
        ("Interview invitation.", {"subject": "[req-42]", "expected_candidates": [A], "expected_confident": A}),
        ("We invite you to an interview.", {"subject": "REQ-73", "expected_candidates": [B], "expected_confident": B}),
        ("邀请您参加面试。", {"subject": "REQ-420"}),
        ("面试邀请。", {"subject": "XREQ-42"}),
        ("邀请您参加面试。", {"subject": "REQ-42-B"}),
        ("面试邀请。", {"subject": "[req-42]", "expected_candidates": [A], "expected_confident": A}),
        ("邀请您参加面试。", {"subject": "REQ-73", "expected_candidates": [B], "expected_confident": B}),
    ], INVITED, candidates=())
    assert len(cases) == 120 and len({case["id"] for case in cases}) == 120
    return cases
