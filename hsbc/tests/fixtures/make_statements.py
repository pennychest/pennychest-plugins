"""Makes made-up HSBC statements, laid out as the real ones are, for the importer's tests.

    pip install reportlab
    python hsbc/tests/fixtures/make_statements.py

Writes current_account.pdf and credit_card.pdf next to this file. Every name, number and
transaction is invented. The column positions are those the importer reads real statements
by: dates at 53pt, payment types at 112pt, descriptions at 140pt, and the paid out, paid in
and balance columns right-aligned under their headers.
"""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

HERE = Path(__file__).parent
WIDTH, HEIGHT = A4


def text(c, x, top, words, size=8):
    """Draws `words` with its left edge at x, `top` points down the page."""
    c.setFont("Helvetica", size)
    c.drawString(x, HEIGHT - top, words)


def right(c, x, top, words, size=8):
    c.setFont("Helvetica", size)
    c.drawRightString(x, HEIGHT - top, words)


def ca_header(c, top):
    text(c, 53, top, "Date")
    text(c, 112, top, "Paymenttype")
    text(c, 165, top, "and")
    text(c, 182, top, "details")
    text(c, 357, top, "Paidout")
    text(c, 430, top, "Paidin")
    text(c, 505, top, "Balance")


def ca_row(c, top, date="", kind="", details="", out="", paid_in="", balance=""):
    if date:
        text(c, 53, top, date)
    if kind:
        text(c, 112, top, kind)
    if details:
        text(c, 140, top, details)
    if out:
        right(c, 410, top, out)
    if paid_in:
        right(c, 485, top, paid_in)
    if balance:
        right(c, 560, top, balance)


def current_account():
    c = canvas.Canvas(str(HERE / "current_account.pdf"), pagesize=A4)
    text(c, 53, 40, "HSBC UK", 12)
    text(c, 53, 70, "Your HSBC Advance details", 10)
    text(c, 300, 70, "27 November to 26 December 2025")
    text(c, 53, 90, "Account Name Sortcode Account Number Sheet Number")
    text(c, 53, 102, "Alice Jane Example 40-11-22 31926819 7")
    # HSBC prints these labels as one word.
    text(c, 53, 125, "OpeningBalance 1,000.00")
    text(c, 53, 137, "Paymentsin 2,600.00")
    text(c, 53, 149, "Paymentsout 1,365.44")
    text(c, 53, 161, "ClosingBalance 2,234.56")
    ca_header(c, 200)
    ca_row(c, 215, "27 Nov 25", details="BALANCEBROUGHTFORWARD", balance="1,000.00")
    ca_row(c, 230, "28 Nov 25", "VIS", "TESCO STORES 2041", "12.50")
    ca_row(c, 242, kind=")))", details="COSTA COFFEE", out="3.20", balance="984.30")
    ca_row(c, 257, "01 Dec 25", "CR", "EXAMPLE EMPLOYER LTD", paid_in="2,500.00")
    ca_row(c, 269, details="SALARY DEC")
    ca_row(c, 284, "02 Dec 25", "DD", "COUNCIL TAX", "150.00", balance="3,334.30")
    ca_row(c, 299, "05 Dec 25", "VIS", "IAPAPP STORE", "0.99")
    # The page ends mid-transaction: its amount is at the top of the next.
    ca_row(c, 314, "08 Dec 25", "SO", "RENT TO LANDLORD")
    c.showPage()
    text(c, 53, 40, "HSBC UK", 12)
    ca_header(c, 80)
    ca_row(c, 95, details="REF FLAT 2", out="1,198.75", balance="2,134.56")
    ca_row(c, 110, "20 Dec 25", "BP", "J SMITH", paid_in="100.00")
    ca_row(c, 125, "26 Dec 25", details="BALANCECARRIEDFORWARD", balance="2,234.56")
    c.save()


def cc_header(c, top):
    right(c, 560, top - 8, "Amount")
    text(c, 40, top, "ReceivedByUs")
    text(c, 110, top, "TransactionDate")
    text(c, 185, top, "Details")


def cc_row(c, top, received, made, details, amount):
    for x, part in zip((40, 52, 68), received.split()):
        text(c, x, top, part)
    for x, part in zip((110, 122, 138), made.split()):
        text(c, x, top, part)
    text(c, 185, top, details)
    right(c, 560, top, amount)


def credit_card():
    c = canvas.Canvas(str(HERE / "credit_card.pdf"), pagesize=A4)
    text(c, 40, 40, "HSBC UK", 12)
    text(c, 40, 70, "Your Credit Card statement", 10)
    text(c, 40, 90, "Mr Bob Charles Example 4000 1234 5678 9010")
    text(c, 300, 90, "Statement Date 27 June 2026")
    text(c, 40, 110, "PreviousBalance 312.56")
    text(c, 40, 122, "New Balance 1,025.23")
    cc_header(c, 160)
    cc_row(c, 180, "29 May 26", "28 May 26", "TESCO STORES 2041", "43.50")
    cc_row(c, 195, "02 Jun 26", "02 Jun 26", ")))PRET A MANGER", "6.95")
    cc_row(c, 210, "05 Jun 26", "03 Jun 26", "AMAZON REFUND", "19.99CR")
    cc_row(c, 225, "10 Jun 26", "10 Jun 26", "PAYMENT RECEIVED - THANK YOU", "312.56CR")
    c.showPage()
    # A continuation page with no header.
    text(c, 40, 40, "HSBC UK", 12)
    cc_row(c, 80, "20 Jun 26", "18 Jun 26", "TRAINLINE.COM", "1,007.83")
    text(c, 40, 120, "Total")
    right(c, 560, 120, "1,058.28")
    c.save()


if __name__ == "__main__":
    current_account()
    credit_card()
