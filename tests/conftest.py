"""Pytest configuration for the price_monitor test suite."""


def pytest_addoption(parser):
    parser.addoption(
        "--real-llm",
        action="store_true",
        default=False,
        help="Run eval against real LLM (requires LLM_API_KEY)",
    )
