from backend.app.text_processing import SentenceSpan, split_sentences, tokenize_sentence


def test_mixed_language_sentence_splitting() -> None:
    text = "The company reduced emissions. 这是第二句！Next line?\n最后一行"

    sentences = split_sentences(text)

    assert [sentence.text for sentence in sentences] == [
        "The company reduced emissions.",
        "这是第二句！",
        "Next line?",
        "最后一行",
    ]


def test_token_offsets_are_document_offsets() -> None:
    sentence = SentenceSpan(index=0, text="carbon emissions 减排50%", start=7, end=29)

    tokens = tokenize_sentence(sentence)

    assert tokens[0].text == "carbon"
    assert tokens[0].start == 7
    assert tokens[0].end == 13
    assert tokens[2].text == "减"
    assert tokens[2].start == 24


def test_tokenization_keeps_decimal_percent_and_model_names_together() -> None:
    text = "Revenue rose 3.5% on model 5.5-low."
    sentence = SentenceSpan(index=0, text=text, start=0, end=len(text))

    tokens = tokenize_sentence(sentence)

    assert [(token.text, token.start, token.end) for token in tokens] == [
        ("Revenue", 0, 7),
        ("rose", 8, 12),
        ("3.5%", 13, 17),
        ("on", 18, 20),
        ("model", 21, 26),
        ("5.5-low", 27, 34),
        (".", 34, 35),
    ]


def test_empty_text_has_no_sentences() -> None:
    assert split_sentences("\n\n   ") == []


def test_punctuation_only_text_does_not_crash() -> None:
    sentences = split_sentences("!!!")

    assert [sentence.text for sentence in sentences] == ["!!!"]


def test_multiline_text_uses_newlines_as_boundaries() -> None:
    sentences = split_sentences("First line\nSecond line")


    assert [sentence.text for sentence in sentences] == ["First line", "Second line"]


def test_sentence_splitting_keeps_chinese_closing_quote_with_sentence() -> None:
    sentences = split_sentences("他说：“可能会下雨。”然后他离开。")

    assert [sentence.text for sentence in sentences] == ["他说：“可能会下雨。”", "然后他离开。"]
    assert sentences[0].start == 0
    assert sentences[0].end == len("他说：“可能会下雨。”")
    assert sentences[1].start == len("他说：“可能会下雨。”")


def test_sentence_splitting_keeps_english_closing_quote_with_sentence() -> None:
    sentences = split_sentences('She said, "It may work." Then left.')

    assert [sentence.text for sentence in sentences] == ['She said, "It may work."', "Then left."]


def test_sentence_splitting_keeps_common_english_abbreviations_inside_sentence() -> None:
    sentences = split_sentences("U.S. officials said counting may continue. Dr. Chen said the audit was clear.")

    assert [sentence.text for sentence in sentences] == [
        "U.S. officials said counting may continue.",
        "Dr. Chen said the audit was clear.",
    ]


def test_sentence_splitting_allows_sentence_boundary_after_final_initialism_period() -> None:
    sentences = split_sentences("Observers monitored the U.S. It mattered. The U.S. Senate met.")

    assert [sentence.text for sentence in sentences] == [
        "Observers monitored the U.S.",
        "It mattered.",
        "The U.S. Senate met.",
    ]


def test_sentence_splitting_keeps_mixed_terminal_punctuation_together() -> None:
    sentences = split_sentences("真的吗？！他说不是。What?! They denied it.")

    assert [sentence.text for sentence in sentences] == [
        "真的吗？！",
        "他说不是。",
        "What?!",
        "They denied it.",
    ]
