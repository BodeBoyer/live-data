import json
from types import SimpleNamespace

from pcflip.valuation.llm import LlmExtractor, extract_with_llm, merge_llm, SCHEMA
from pcflip.valuation.extract import extract


class FakeMessages:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(self.payload))])


def fake_client(payload):
    return SimpleNamespace(messages=FakeMessages(payload))


def test_llm_fills_gaps_but_cannot_invent(watchlist, tmp_path):
    payload = {"is_prebuilt": True, "is_laptop": False, "is_wanted_ad": False, "gpu": "RTX 4070 Ti Super",
               "cpu": "Core i7 14700K", "ram_gb": 32, "ram_type": "ddr5", "ram_is_sodimm": False,
               "drives": [{"capacity_gb": 2000, "kind": "nvme"}, {"capacity_gb": 4000, "kind": "hdd"}],
               "condition_problems": [], "missing_parts": [], "confidence": "high"}
    client = fake_client(payload)
    llm = LlmExtractor(client=client)
    # a listing whose description is prose the regexes can't fully parse
    ex = extract_with_llm("Gaming computer, great condition", "Has the fourty-seventy ti super card, an i7 14700K, thirty two gigs of DDR5 and a two terabyte nvme plus a 4TB hard drive.",
                          watchlist, llm, only_for_prebuilts=True)
    keys = sorted(c.key for c in ex.components)
    assert keys == ["ddr5-32gb", "i7-14700k", "nvme-2tb", "rtx-4070-ti-super"]
    assert ex.kind == "prebuilt" and ex.method == "regex+llm"
    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["schema"] is SCHEMA
    assert call["output_config"]["effort"] == "low"


def test_llm_unknown_model_is_noted_not_priced(watchlist):
    payload = {"is_prebuilt": True, "is_laptop": False, "is_wanted_ad": False, "gpu": "Voodoo 5 6000", "cpu": None,
               "ram_gb": None, "ram_type": None, "ram_is_sodimm": False, "drives": [], "condition_problems": ["won't boot"],
               "missing_parts": [], "confidence": "low"}
    ex = merge_llm(extract("Old gaming pc", "for collectors", watchlist), payload, watchlist)
    assert ex.components == []
    assert any("not in the watchlist" in n for n in ex.notes)
    assert "seller states: won't boot" in ex.hard_flags


def test_llm_not_called_when_regex_is_complete(watchlist):
    client = fake_client({})
    llm = LlmExtractor(client=client)
    extract_with_llm("Gaming PC RTX 4070 7800X3D 32GB DDR5 2TB NVMe", "complete", watchlist, llm)
    assert client.messages.calls == []
    extract_with_llm("RTX 3090", "works", watchlist, llm)  # single part, not a prebuilt
    assert client.messages.calls == []
