"""Reviewed official model metadata; prices are USD per million tokens, 2026-11-02.

DeepSeek peak rates intentionally overestimate off-peak usage. Receipts remain
estimates rather than pretending to be the provider's final invoice.
"""
from decimal import Decimal, ROUND_CEILING

MODELS = {
    'deepseek': [
        {'id': 'deepseek-flash', 'name': 'DeepSeek Flash', 'input': '0.30', 'cached': '0.006', 'output': '1.20'},
        {'id': 'deepseek-v4-pro', 'name': 'DeepSeek Pro', 'input': '1.32', 'cached': '0.044', 'output': '3.96'},
    ],
    'openai': [
        {'id': 'gpt-6.1-sol', 'name': 'GPT-6.1 Sol', 'input': '2', 'cached': '0.1', 'cache_write': '2.5', 'output': '10'},
        {'id': 'gpt-6-astra', 'name': 'GPT-6 Astra', 'input': '10', 'cached': '1', 'cache_write': '12.5', 'output': '50'},
        {'id': 'gpt-6.1-terra', 'name': 'GPT-6.1 Terra', 'input': '1', 'cached': '0.05', 'cache_write': '1.25', 'output': '5'},
        {'id': 'gpt-6-sol', 'name': 'GPT-6 Sol', 'input': '2', 'cached': '0.2', 'cache_write': '2.5', 'output': '10'},
        {'id': 'gpt-6-luna', 'name': 'GPT-6 Luna', 'input': '0.1', 'cached': '0.01', 'cache_write': '0.125', 'output': '0.5'},
        {'id': 'gpt-5.6-sol', 'name': 'GPT-5.6 Sol', 'input': '4', 'cached': '0.4', 'cache_write': '5', 'output': '20'},
        {'id': 'gpt-5.6-terra', 'name': 'GPT-5.6 Terra', 'input': '2', 'cached': '0.2', 'cache_write': '2.5', 'output': '12'},
        {'id': 'gpt-5.6-luna', 'name': 'GPT-5.6 Luna', 'input': '0.2', 'cached': '0.02', 'cache_write': '0.25', 'output': '1.2'},
    ],
}
PRICE_VERSION = 'pricing-2026-11-02'
MODEL_CATALOG_VERSION = 'openai-common-models-2026-11-02'
OPENAI_MODEL_IDS = tuple(model['id'] for model in MODELS['openai'])
# Public official sources for the reviewed standard text rates above. Cached
# writes use 30-minute retention; requests do not opt into longer retention.
OPENAI_PRICE_SOURCES = {
    model: 'https://developers.openai.com/api/docs/models/' + model
    for model in OPENAI_MODEL_IDS
}
LEGACY_INPUT_BYTES = 48 * 1024
MAX_INPUT_BYTES = 96 * 1024
MAX_RESPONSE_BYTES = 192 * 1024


def price(provider, model):
    return next((x for x in MODELS[provider] if x['id'] == model), None)


def max_output(provider, probe=False):
    return (128 if probe else 3072) if provider == 'deepseek' else (1024 if probe else 6144)


def estimated_nusd(provider, model, input_tokens, output_tokens, cached_tokens=0, cache_write_tokens=0, *, rates=None):
    rates = rates or price(provider, model)
    if rates is None:
        raise ValueError('unknown_price')
    # Cache writes are separate billable input categories, never ordinary hits.
    plain = max(0, input_tokens - cached_tokens - cache_write_tokens)
    amount = (Decimal(plain) * Decimal(rates['input']) + Decimal(cached_tokens) * Decimal(rates['cached'])
              + Decimal(cache_write_tokens) * Decimal(rates.get('cache_write', rates['input']))
              + Decimal(output_tokens) * Decimal(rates['output']))
    return int((amount * 1000).to_integral_value(rounding=ROUND_CEILING))


def usd(nusd):
    return str(Decimal(nusd) / Decimal(1000000000))
