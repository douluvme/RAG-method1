from docqa.verify import best_part, find_quote, is_supported

PAGE = (
    "The term of this agree-\nment is three (3) years, starting on 1 January 2027.\n"
    "Either party may terminate it with “sixty days” written notice."
)


def test_exact_and_formatting_differences():
    assert is_supported(PAGE, "The term of this agreement is three (3) years")
    assert is_supported(PAGE, 'either party may terminate it with "sixty days" written notice.')


def test_ellipsis_pieces():
    assert is_supported(PAGE, "The term of this agreement ... starting on 1 January 2027")


def test_minor_typo_tolerated_but_fabrication_rejected():
    assert is_supported(PAGE, "Either party may terminate it with sixty day written notice")
    assert not is_supported(PAGE, "The term of this agreement is five (5) years, ending in 2030.")
    assert not is_supported(PAGE, "The landlord pays for all utilities and repairs.")
    assert not is_supported(PAGE, "")


def test_cjk_with_line_breaks():
    text = "회의는 매주 화요일\n오전 10시에 열립니다. 会議は毎週\n火曜日に行われます。"
    assert is_supported(text, "회의는 매주 화요일 오전 10시에 열립니다.")
    assert is_supported(text, "会議は毎週火曜日に行われます")
    assert not is_supported(text, "회의는 매주 금요일 오후 3시에 열립니다.")


def test_best_part():
    parts = ["Intro text.", "The budget is 4.2 million.", "Marketing gets 15 percent."]
    assert best_part(parts, "Marketing gets 15 percent") == 2
    assert best_part(parts, "the budget is 4.2 million") == 1


def test_numbers_must_match_exactly():
    text = "The tenant shall pay rent of 1,200 dollars per month for 36 months."
    assert is_supported(text, "The tenant shall pay rent of 1,200 dollars per month")
    assert not is_supported(text, "The tenant shall pay rent of 1,500 dollars per month")
    assert not is_supported(text, "rent of 1,200 dollars per month for 38 months")


def test_returns_document_wording():
    # The AI's quote has a typo, curly quotes and lost hyphenation;
    # the user is shown the real text.
    assert find_quote(PAGE, "Either party may terminat it with \"sixty days\" written notice") == \
        "Either party may terminate it with “sixty days” written notice"
    assert find_quote(PAGE, "the term of this agreement is three (3) years") == \
        "The term of this agreement is three (3) years"
    assert find_quote(PAGE, "The term of this agreement ... 1 January 2027") == \
        "The term of this agreement … 1 January 2027"
