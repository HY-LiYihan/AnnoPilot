from backend.app.settings import get_llm_model_option, selected_llm_model_option_id


def test_stable_model_options_map_to_provider_model_ids() -> None:
    assert get_llm_model_option("gpt5.5-medium").model == "gpt-5.5"
    assert get_llm_model_option("gpt5.6-low").model == "gpt-5.6-luna"
    assert get_llm_model_option("gpt5.6-medium").model == "gpt-5.6-sol"
    assert get_llm_model_option("gpt5.6-high").model == "gpt-5.6-terra"


def test_provider_model_names_select_stable_ui_options() -> None:
    assert selected_llm_model_option_id("gpt-5.5") == "gpt5.5-medium"
    assert selected_llm_model_option_id("gpt-5.6-luna") == "gpt5.6-low"
    assert selected_llm_model_option_id("gpt-5.6-sol") == "gpt5.6-medium"
    assert selected_llm_model_option_id("gpt-5.6-terra") == "gpt5.6-high"
