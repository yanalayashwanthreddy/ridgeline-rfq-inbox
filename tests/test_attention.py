"""`attention`: non-requests that still need a person. needs_review stays exactly per README."""

from rfq.attention import attention_for
from rfq.ingest import Email, Part


def em(subject="", body="", sender="a@customer.example", parts=()):
    return Email(id="X", sender=sender, subject=subject, body=body, parts=list(parts))


def att(e, cls, reason=""):
    return attention_for(e, {"classification": cls}, reason)


def test_requests_never_get_attention():
    for cls in ("rfq", "rfq_revision", "purchase_order"):
        assert att(em("RFQ"), cls) is None


def test_a_customer_waiting_on_status_needs_a_reply():
    a = att(em("RE: Quote Q-26-04417", "any update?"), "status_inquiry")
    assert "Q-26-04417" in a and "reply" in a


def test_our_bounced_email_says_who_and_what():
    orig = Part("original_message.eml", "message/rfc822", 1, "x",
                text="From: Ridgeline RFQ Desk <rfq@ridgeline-supply.example>\nSubject: Quote request - valves")
    a = att(em("Delivery Status Notification (Failure)", "Delivery to sales@acme.example failed permanently",
               sender="MAILER-DAEMON@mx1.ridgeline-supply.example", parts=[orig]), "auto_reply")
    assert "sales@acme.example" in a and "Quote request - valves" in a and "resend" in a


def test_out_of_office_needs_nothing():
    assert att(em("Automatic reply: Q-26-04490", "I am out of the office until Monday"), "auto_reply") is None


def test_lost_deal_is_closed_with_its_quote_number():
    a = att(em("Re: Quote Q-26-04512", "Thanks, but we went with another supplier on this one."), "other")
    assert "lost" in a and "Q-26-04512" in a


def test_invoice_goes_to_accounting_with_its_number():
    a = att(em("Invoice INV-88412", "Please find attached invoice for PO RL-PO-5531."), "other")
    assert "accounting" in a and "INV-88412" in a


def test_marketing_needs_nothing():
    assert att(em("FALL FLANGE SALE - 30% off", "Order today!"), "other") is None


def test_vendor_quote_is_routed():
    assert "route" in att(em("Quotation GCV-55120 for Ridgeline"), "vendor_quote")
