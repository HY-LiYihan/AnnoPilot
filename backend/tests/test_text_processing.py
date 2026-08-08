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


def test_empty_text_has_no_sentences() -> None:
    assert split_sentences("\n\n   ") == []


def test_punctuation_only_text_does_not_crash() -> None:
    sentences = split_sentences("!!!")

    assert [sentence.text for sentence in sentences] == ["!", "!", "!"]


def test_multiline_text_uses_newlines_as_boundaries() -> None:
    sentences = split_sentences("First line\nSecond line")


    assert [sentence.text for sentence in sentences] == ["First line", "Second line"]
