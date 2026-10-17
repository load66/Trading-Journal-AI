"""Mock-only budget and privacy regression checks for Finance Groq fallback."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from finance_ai_categories import classify_unknown, cache_key, sanitized_description


def state():
    return {"transactions":{"synthetic-account-key":{
        "synthetic-tx":{"amount":"-45.65","description":"Odd merchant 6789012",
                        "posted":1792120000,"pending":False,"kind":"withdrawal"}
    }}}


class Reply:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status
    def json(self):
        return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps(self.payload)}}]}


def test_groq_optional_batch_is_sanitized_and_cached(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY","synthetic-not-secret")
    monkeypatch.delenv("FINANCE_CATEGORY_MODEL",raising=False)
    s=state()
    seen=[]
    def mock_post(url,**kwargs):
        seen.append((url,kwargs))
        return Reply({"items":[{"i":0,"category":"dining","confidence":0.89}]})
    assert classify_unknown(s,post=mock_post,now=1792134000)==1
    assert len(seen)==1
    assert s["financeAICache"][cache_key("Odd merchant 6789012")]["category"]=="dining"
    transmitted=json.dumps(seen[0][1]["json"]["messages"][1])
    assert "6789012" not in transmitted
    assert "1792120000" not in transmitted
    assert "synthetic-account-key" not in transmitted
    assert "45.65" not in transmitted
    assert "Odd merchant" in transmitted
    assert classify_unknown(s,post=mock_post,now=1792134000)==0
    assert len(seen)==1


def test_429_and_bad_reply_leave_activity_unmodified(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY","synthetic-not-secret")
    s=state()
    original=s["transactions"]["synthetic-account-key"]["synthetic-tx"].copy()
    assert classify_unknown(s,post=lambda *_a,**_kw:Reply({},429),now=1792134000)==0
    assert s["financeAICache"]=={}
    assert s["transactions"]["synthetic-account-key"]["synthetic-tx"]==original
    assert classify_unknown(s,post=lambda *_a,**_kw:Reply({"items":[
        {"i":0,"category":"transfer","confidence":"high"}]}),now=1792134000)==0
    assert s["financeAICache"]=={}
    attempts=[]
    assert classify_unknown(s,post=lambda *_a,**_kw:attempts.append(True),now=1792134000)==0
    assert not attempts, "Two requests per UTC day, including failures"


def test_no_key_never_calls_provider(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY",raising=False)
    called=[]
    assert classify_unknown(state(),post=lambda *_a,**_kw:called.append(True))==0
    assert called==[]


def test_description_is_redacted_and_bounded():
    assert "customer@example" not in sanitized_description("Transfer customer@example.com 1234567")
    assert "1234567" not in sanitized_description("Transfer 1234567")
    assert len(sanitized_description("words "*100))<=96


def test_ambiguous_ai_subscription_requires_high_confidence(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY","synthetic-only")
    s=state()
    r=Reply({"items":[{"i":0,"category":"subscription","confidence":0.83}]})
    assert classify_unknown(s,post=lambda *_a,**_k:r,now=1792134000)==0
    assert s["financeAILastSync"]=={"attempted":1,"categorized":0,"failed":1,"at":1792134000}
    assert not s["financeAICache"]
