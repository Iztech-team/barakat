"""End-to-end proof of debt repayment against a real bench.

    bench --site shop1.barakat.local execute barakat.e2e_repayment.run

Proves the whole story on real documents rather than mocks: a credit sale
creates debt, an over-payment is refused, a repayment before shift close lands
on account, consolidation turns it into a Sales Invoice, a repayment after that
allocates to the invoice, and a customer who owes nothing cannot pay.
"""

import frappe

PASS, FAIL = [], []


def check(name, got, want):
    if got == want:
        PASS.append(name)
        print(f"  ok   {name}")
    else:
        FAIL.append(f"{name}: got {got!r}, want {want!r}")
        print(f"  FAIL {name}: got {got!r}, want {want!r}")


def check_true(name, got):
    check(name, bool(got), True)


def throws(name, fn, needle):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — the point is to see any refusal
        text = str(exc)
        if needle.lower() in text.lower():
            PASS.append(name)
            print(f"  ok   {name}")
        else:
            FAIL.append(f"{name}: threw {text[:160]!r}, wanted {needle!r}")
            print(f"  FAIL {name}: threw {text[:160]!r}, wanted {needle!r}")
        return
    FAIL.append(f"{name}: did not throw")
    print(f"  FAIL {name}: did not throw")


def run():
    from barakat.api.credit import (
        get_customer_credit,
        record_customer_repayment,
        set_customer_credit_limit,
    )

    frappe.set_user("Administrator")
    company = frappe.defaults.get_user_default("Company") or frappe.get_all(
        "Company", pluck="name"
    )[0]
    # Prefer a profile that already has a till cash account — the repayment
    # needs somewhere for the money to land, and a profile without one is not a
    # till anybody could be using.
    candidates = frappe.get_all(
        "POS Profile",
        filters={"company": company},
        fields=["name", "custom_cash_account"],
    )
    ready = [c for c in candidates if c.custom_cash_account]
    profile = (ready or candidates)[0].name
    prof = frappe.get_doc("POS Profile", profile)
    # A credit sale IS a partial payment as far as ERPNext core is concerned,
    # and it refuses one unless the profile allows it. The proxy forces this on
    # for every profile it creates; set it here so the test runs against the
    # same shape a real till has.
    if not prof.allow_partial_payment:
        prof.db_set("allow_partial_payment", 1)
        prof.reload()
        print("enabled allow_partial_payment on the profile")
    mode = prof.payments[0].mode_of_payment
    item = frappe.get_all(
        "Item", filters={"is_stock_item": 0, "disabled": 0}, pluck="name"
    )
    item = item[0] if item else frappe.get_all("Item", pluck="name")[0]
    print(f"company={company} profile={profile} mode={mode} item={item}")

    # A POS Invoice will not validate without an open shift for its profile —
    # the same gate the till satisfies when the cashier opens the till.
    opening = frappe.get_all(
        "POS Opening Entry",
        filters={"pos_profile": profile, "status": "Open", "docstatus": 1},
        pluck="name",
    )
    if opening:
        print(f"reusing open shift {opening[0]}")
    else:
        po = frappe.get_doc(
            {
                "doctype": "POS Opening Entry",
                "period_start_date": frappe.utils.now(),
                "posting_date": frappe.utils.nowdate(),
                "company": company,
                "pos_profile": profile,
                "user": frappe.session.user,
                "balance_details": [
                    {"mode_of_payment": mode, "opening_amount": 0}
                ],
            }
        )
        po.insert()
        po.submit()
        print(f"opened shift {po.name}")

    stamp = frappe.utils.random_string(6)
    cust = frappe.get_doc(
        {
            "doctype": "Customer",
            "customer_name": f"Repay E2E {stamp}",
            "customer_type": "Individual",
        }
    ).insert()
    set_customer_credit_limit(cust.name, company, 1000)
    print(f"customer={cust.name}")

    def credit_sale(total, paid):
        inv = frappe.get_doc(
            {
                "doctype": "POS Invoice",
                "customer": cust.name,
                "company": company,
                "pos_profile": profile,
                "is_pos": 1,
                "set_warehouse": prof.warehouse,
                "update_stock": 0,
                "items": [{"item_code": item, "qty": 1, "rate": total}],
                "payments": [{"mode_of_payment": mode, "amount": paid}],
            }
        )
        inv.set_missing_values()
        inv.insert()
        inv.submit()
        return inv

    owed = lambda: get_customer_credit(cust.name, company)["owed"]  # noqa: E731

    print("\n1. a credit sale creates debt the GL cannot see yet")
    inv1 = credit_sale(300, 100)
    check("owes the unpaid remainder", owed(), 200.0)
    check(
        "and none of it is in the GL",
        get_customer_credit(cust.name, company)["consolidated"],
        0.0,
    )

    print("\n2. the cap is enforced server-side")
    throws(
        "paying more than owed is refused",
        lambda: record_customer_repayment(cust.name, company, 250, mode, profile),
        "more than",
    )
    throws(
        "zero is refused",
        lambda: record_customer_repayment(cust.name, company, 0, mode, profile),
        "greater than zero",
    )
    throws(
        "a negative amount is refused",
        lambda: record_customer_repayment(cust.name, company, -50, mode, profile),
        "greater than zero",
    )
    check("and none of that changed the debt", owed(), 200.0)

    print("\n3. repaying before shift close goes on account")
    res = record_customer_repayment(
        cust.name, company, 50, mode, profile, external_id=f"e2e-{stamp}-1"
    )
    check("the key it was sent comes back", res["externalId"], f"e2e-{stamp}-1")
    check("and it is a first payment, not a retry", res["reused"], False)
    check("nothing could be allocated to an invoice", res["allocated"], [])
    check("so the whole payment is on account", res["onAccount"], 50.0)
    check("and the debt drops", res["owedAfter"], 150.0)
    pe = frappe.get_doc("Payment Entry", res["paymentEntry"])
    check("the payment entry is submitted", pe.docstatus, 1)
    check("it receives money", pe.payment_type, "Receive")
    check("from this customer", pe.party, cust.name)
    check("into the till's own cash account", pe.paid_to, prof.custom_cash_account)
    check_true("and it wrote GL entries", frappe.db.exists("GL Entry", {"voucher_no": pe.name}))

    print("\n3b. the same repayment twice is one payment")
    again = record_customer_repayment(
        cust.name, company, 50, mode, profile, external_id=res["externalId"]
    )
    check(
        "the second call returns the first entry",
        again["paymentEntry"],
        res["paymentEntry"],
    )
    check("and says so plainly", again["reused"], True)
    check("and the debt did not move again", owed(), 150.0)
    check(
        "only one payment entry exists for that key",
        frappe.db.count("Payment Entry", {"custom_external_id": res["externalId"]}),
        1,
    )

    print("\n4. consolidation turns the debt into a Sales Invoice")
    from erpnext.accounts.doctype.pos_invoice_merge_log.pos_invoice_merge_log import (
        consolidate_pos_invoices,
    )

    # The merge log reads `.pos_invoice` off each row (they are normally
    # POS Invoice Reference children), so a plain dict will not do.
    consolidate_pos_invoices(
        pos_invoices=[
            frappe._dict({"pos_invoice": inv1.name, "customer": cust.name})
        ]
    )
    inv1.reload()
    check_true("the POS Invoice is merged", bool(inv1.consolidated_invoice))
    si = frappe.get_doc("Sales Invoice", inv1.consolidated_invoice)
    check("the debt is unchanged by consolidating", owed(), 150.0)

    print("\n5. now a repayment allocates to that invoice")
    before = si.outstanding_amount
    res2 = record_customer_repayment(cust.name, company, 100, mode, profile)
    check("one invoice was settled against", len(res2["allocated"]), 1)
    check("and it is the consolidated one", res2["allocated"][0]["invoice"], si.name)
    check("for the full amount paid", res2["allocated"][0]["amount"], 100.0)
    check("with nothing left on account", res2["onAccount"], 0.0)
    si.reload()
    check(
        "the invoice's own outstanding fell by the payment",
        round(before - si.outstanding_amount, 2),
        100.0,
    )
    check("and the debt fell too", res2["owedAfter"], 50.0)

    print("\n6. paying off the rest clears the customer")
    res3 = record_customer_repayment(cust.name, company, 50, mode, profile)
    check("the debt is gone", res3["owedAfter"], 0.0)
    throws(
        "and a customer who owes nothing cannot pay",
        lambda: record_customer_repayment(cust.name, company, 10, mode, profile),
        "does not owe",
    )

    print("\n6b. a customer may settle by card, and it goes to the card account")
    # Another debt to settle, this time not in cash.
    inv2 = credit_sale(200, 0)
    card = None
    for row in prof.payments:
        mode_type = frappe.db.get_value(
            "Mode of Payment", row.mode_of_payment, "type"
        )
        if mode_type != "Cash":
            card = row.mode_of_payment
            break
    if not card:
        print("  .... skipped: this profile offers no non-cash method")
    else:
        card_account = frappe.db.get_value(
            "Mode of Payment Account",
            {"parent": card, "company": company},
            "default_account",
        )
        res4 = record_customer_repayment(
            cust.name, company, 200, card, profile, external_id=f"e2e-{stamp}-card"
        )
        pe4 = frappe.get_doc("Payment Entry", res4["paymentEntry"])
        check("the card repayment is submitted", pe4.docstatus, 1)
        check("it names the method the customer used", pe4.mode_of_payment, card)
        check("and it does NOT land in the till drawer", pe4.paid_to != prof.custom_cash_account, True)
        if card_account:
            check("it lands in the card's own account", pe4.paid_to, card_account)
        check("the debt is cleared all the same", res4["owedAfter"], 0.0)

    print("\n6c. a method this till does not offer is refused")
    # Give them a debt first. The debt checks run before the method check —
    # deliberately, because "this customer owes nothing" is the message a
    # cashier can act on and a bad method is a programming error. Without a
    # debt here the test would pass on the wrong refusal.
    inv3 = credit_sale(50, 0)
    throws(
        "an unknown mode cannot take money",
        lambda: record_customer_repayment(
            cust.name, company, 10, "Definitely Not A Mode", profile
        ),
        "not a payment method",
    )
    check("and the debt is untouched by the refusal", owed(), 50.0)
    # Put them back to zero so section 7 still measures full headroom.
    record_customer_repayment(
        cust.name, company, 50, mode, profile, external_id=f"e2e-{stamp}-clear"
    )
    check("cleared again", owed(), 0.0)
    assert inv3.name

    print("\n6d. a repayment is tied to its shift, and listable")
    from barakat.api.credit import list_customer_repayments, list_shift_repayments

    oe = frappe.get_all(
        "POS Opening Entry",
        filters={"pos_profile": profile, "docstatus": 1},
        pluck="name",
        order_by="creation desc",
        limit=1,
    )[0]
    inv4 = credit_sale(60, 0)
    res5 = record_customer_repayment(
        cust.name, company, 60, mode, profile,
        external_id=f"e2e-{stamp}-shift", pos_opening_entry=oe,
    )
    pe5 = frappe.get_doc("Payment Entry", res5["paymentEntry"])
    check("the payment is stamped with the shift", pe5.custom_pos_opening_entry, oe)

    shift_list = list_shift_repayments(oe)
    names = [r["name"] for r in shift_list["repayments"]]
    check("the shift listing finds it", res5["paymentEntry"] in names, True)
    check("and totals it", shift_list["total"] >= 60, True)

    cust_list = list_customer_repayments(cust.name, company)
    all_names = [r["name"] for r in cust_list["repayments"]]
    check("the customer listing finds it too", res5["paymentEntry"] in all_names, True)
    check(
        "the customer listing counts every repayment made",
        cust_list["total"] >= 4,
        True,
    )
    row = next(r for r in cust_list["repayments"] if r["name"] == res5["paymentEntry"])
    check("the row carries the amount", row["amount"], 60.0)
    check("and the method", row["modeOfPayment"], mode)
    check("and who recorded it", bool(row["recordedBy"]), True)

    print("\n6e. a payment with no shift belongs to no shift")
    inv5 = credit_sale(25, 0)
    res6 = record_customer_repayment(
        cust.name, company, 25, mode, profile, external_id=f"e2e-{stamp}-noshift"
    )
    no_shift = frappe.get_doc("Payment Entry", res6["paymentEntry"])
    check("it carries no shift", no_shift.custom_pos_opening_entry, None)
    check(
        "so the shift listing does not claim it",
        res6["paymentEntry"] not in [r["name"] for r in list_shift_repayments(oe)["repayments"]],
        True,
    )
    check(
        "but the customer still sees it",
        res6["paymentEntry"] in [r["name"] for r in list_customer_repayments(cust.name, company)["repayments"]],
        True,
    )

    print("\n6f. a cancelled repayment is not a payment")
    inv6 = credit_sale(35, 0)
    res7 = record_customer_repayment(
        cust.name, company, 35, mode, profile, external_id=f"e2e-{stamp}-cancel"
    )
    frappe.get_doc("Payment Entry", res7["paymentEntry"]).cancel()
    check(
        "a cancelled payment drops out of the listing",
        res7["paymentEntry"] not in [r["name"] for r in list_customer_repayments(cust.name, company)["repayments"]],
        True,
    )
    assert inv4.name and inv5.name and inv6.name
    # Clear whatever those left so section 7 still measures full headroom.
    left = get_customer_credit(cust.name, company)["owed"]
    if left > 0:
        record_customer_repayment(
            cust.name, company, left, mode, profile, external_id=f"e2e-{stamp}-clear2"
        )
    check("cleared before the headroom check", owed(), 0.0)


    print("\n7. headroom recovers, so they can buy on credit again")
    standing = get_customer_credit(cust.name, company)
    check("full headroom is back", standing["headroom"], 1000.0)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  - {f}")
    frappe.db.commit()
    return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}
