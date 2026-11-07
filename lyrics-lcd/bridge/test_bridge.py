"""
Tests for the bridge that need NO Arduino and NO internet.

  python test_bridge.py

They check the text/paging logic and the whole /sync flow, using a fake Arduino
and fake captions. If these pass, problems are in wiring, the extension or YouTube.
"""

import time

import bridge


# ---------- text helpers ----------
def test_clean_text():
    assert bridge.clean_text("Café [Music] a|b\nc") == "Cafe a b c"
    assert bridge.clean_text("It’s ♪ fine ♪") == "It's fine"
    assert bridge.clean_text("[Applause]") == ""


def test_make_pages_wrapping():
    pages = bridge.make_pages([(1.0, 4.0, "We are the champions, my friends")])
    assert [(p["l1"], p["l2"]) for p in pages] == [
        ("We are the", "champions, my"), ("friends", "")]
    for p in pages:
        assert len(p["l1"]) <= 16 and len(p["l2"]) <= 16
    assert pages[0]["start"] == 1.0 and abs(pages[-1]["end"] - 5.0) < 1e-9


def test_make_pages_clamps_overlap_and_skips_empty():
    pages = bridge.make_pages([(0.0, 10.0, "first"), (2.0, 2.0, "[Music]"), (3.0, 2.0, "second")])
    assert [p["l1"] for p in pages] == ["first", "second"]
    assert pages[0]["end"] == 2.0 or pages[0]["end"] == 3.0   # cut where the next caption starts


def test_find_page():
    pages = bridge.make_pages([(1.0, 2.0, "Hello World"), (3.0, 2.0, "Bye")])
    starts = [p["start"] for p in pages]
    assert bridge.find_page(pages, starts, 0.5) is None
    assert bridge.find_page(pages, starts, 1.0)["l1"] == "Hello World"
    assert bridge.find_page(pages, starts, 2.9)["l1"] == "Hello World"
    assert bridge.find_page(pages, starts, 3.0)["l1"] == "Bye"
    assert bridge.find_page(pages, starts, 5.0) is None


# ---------- full flow with fakes ----------
class FakeSerial:
    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data.decode())

    def close(self):
        pass


class FakeSnippet:
    def __init__(self, start, duration, text):
        self.start, self.duration, self.text = start, duration, text


class FakeApi:
    def fetch(self, video_id, languages=("en",)):
        return [FakeSnippet(1.0, 3.0, "Hello there my friend how are you"),
                FakeSnippet(10.0, 2.0, "Goodbye")]


def reset_state():
    bridge.state.update(video_id=None, title="", status="idle", starts=[], pages=[],
                        last_sent=None, last_sync=0.0, idle_shown=False)
    del bridge.pending_commands[:]


def test_full_sync_flow():
    reset_state()
    fake = FakeSerial()
    bridge.arduino["ser"] = fake
    bridge.arduino["port"] = "FAKE"
    bridge.YouTubeTranscriptApi = FakeApi
    client = bridge.app.test_client()

    # 1. no video open
    client.post("/sync", json={"videoId": None, "title": "", "time": 0})
    assert fake.written[-1] == "LCD|Open a video|on YouTube\n"

    # 2. a video starts: loading, then captions arrive
    r = client.post("/sync", json={"videoId": "abc", "title": "My Song", "time": 0})
    assert fake.written[-1] == "LCD|My Song|Loading...\n"
    for _ in range(50):                      # wait for the loader thread
        if bridge.state["status"] == "ready":
            break
        time.sleep(0.05)
    assert bridge.state["status"] == "ready"

    # 3. synced pages
    client.post("/sync", json={"videoId": "abc", "title": "My Song", "time": 0.2})
    assert fake.written[-1] == "LCD||~ music ~\n"            # before the first caption
    client.post("/sync", json={"videoId": "abc", "title": "My Song", "time": 1.5})
    assert fake.written[-1] == "LCD|Hello there my|friend how are\n"
    client.post("/sync", json={"videoId": "abc", "title": "My Song", "time": 3.5})
    assert fake.written[-1] == "LCD|you|\n"
    client.post("/sync", json={"videoId": "abc", "title": "My Song", "time": 10.5})
    assert fake.written[-1] == "LCD|Goodbye|\n"

    # 4. unchanged screen is not re-sent
    count = len(fake.written)
    client.post("/sync", json={"videoId": "abc", "title": "My Song", "time": 10.6})
    assert len(fake.written) == count

    # 5. ads
    client.post("/sync", json={"videoId": "abc", "title": "My Song", "time": 5, "ad": True})
    assert fake.written[-1] == "LCD|Ad playing...|\n"

    # 6. button commands travel back to the extension, exactly once
    bridge.pending_commands.extend(["PLAY", "NEXT"])
    r = client.post("/sync", json={"videoId": "abc", "title": "My Song", "time": 10.6})
    assert r.get_json()["commands"] == ["PLAY", "NEXT"]
    r = client.post("/sync", json={"videoId": "abc", "title": "My Song", "time": 10.6})
    assert r.get_json()["commands"] == []

    # 7. status endpoint
    s = client.get("/status").get_json()
    assert s["bridge"] is True and s["arduino"] is True and s["captions"] == "ready"


def test_no_captions_and_offset():
    reset_state()
    fake = FakeSerial()
    bridge.arduino["ser"] = fake

    class NoCaps:
        def fetch(self, video_id, languages=("en",)):
            raise bridge.TranscriptsDisabled(video_id)

    bridge.YouTubeTranscriptApi = NoCaps
    client = bridge.app.test_client()
    client.post("/sync", json={"videoId": "zzz", "title": "Silent Video", "time": 0})
    for _ in range(50):
        if bridge.state["status"] != "loading":
            break
        time.sleep(0.05)
    assert bridge.state["status"] == "no_captions"
    assert fake.written[-1] == "LCD|Silent Video|No captions\n"

    # offset: with +1.0 the page that starts at t=1.0 shows at t=0.0
    reset_state()
    bridge.YouTubeTranscriptApi = FakeApi
    bridge.TIME_OFFSET = 1.0
    try:
        client.post("/sync", json={"videoId": "abc", "title": "S", "time": 0})
        for _ in range(50):
            if bridge.state["status"] == "ready":
                break
            time.sleep(0.05)
        client.post("/sync", json={"videoId": "abc", "title": "S", "time": 0.0})
        assert fake.written[-1] == "LCD|Hello there my|friend how are\n"
    finally:
        bridge.TIME_OFFSET = 0.0


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\nAll {len(tests)} tests passed.")
