"""Wizard owner step: only the sender of a message carrying the one-time code becomes the owner."""
import importlib.util
import threading
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("zp_setup", Path(__file__).resolve().parents[1] / "setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def msg(uid, text, chat="private", first="Ann", username="ann"):
    return {"update_id": uid, "message": {"chat": {"type": chat, "id": uid}, "text": text,
                                          "from": {"id": uid, "first_name": first, "username": username}}}


class OwnerTests(unittest.TestCase):
    def wiz(self, *batches):
        w = setup.Wizard()
        w.target, w.token, w.bot = object(), "1:x", {"username": "pagerbot", "name": "P"}
        q = list(batches)
        w.poll = lambda timeout: q.pop(0) if q else []
        return w

    def code(self, w):
        r = w.get_owner({})
        self.assertTrue(r["waiting"])
        self.assertTrue(r["link"].startswith("https://t.me/pagerbot?start="))
        return r["code"]

    def test_confirm_of_another_id_is_refused_and_reset_drops_the_candidate(self):
        w = self.wiz([])
        c = self.code(w)
        w.poll = lambda timeout: [msg(7, "/start " + c)]
        self.assertEqual(w.get_owner({})["owner"]["id"], 7)
        self.assertFalse(w.get_owner({"confirm": 8})["ok"])
        self.assertIsNone(w.owner)
        w.get_owner({"reset": True})
        self.assertIsNone(w.owner_cand)
        self.assertFalse(w.get_owner({"confirm": 7})["ok"])
        self.assertIsNone(w.owner)

    def test_stale_private_message_without_code_is_ignored(self):
        w = self.wiz([msg(5, "hello")], [msg(5, "/start")])
        self.code(w)
        self.assertFalse(w.get_owner({})["ok"])
        self.assertIsNone(w.owner)

    def test_correct_code_returns_identity_and_needs_confirm(self):
        w = self.wiz([])
        c = self.code(w)
        w.poll = lambda timeout: [msg(7, "/start " + c)]
        r = w.get_owner({})
        self.assertTrue(r["ok"])
        self.assertEqual(r["owner"], {"id": 7, "name": "Ann", "username": "ann"})
        self.assertIsNone(w.owner)  # not final until confirmed
        self.assertTrue(w.get_owner({"confirm": 7})["ok"])
        self.assertEqual(w.owner["id"], 7)
        w.poll = lambda timeout: [msg(8, "/start " + c)]
        self.assertEqual(w.get_owner({})["owner"]["id"], 7)

    def test_peek_returns_code_without_polling(self):
        w = self.wiz()
        w.poll = lambda timeout: self.fail("polled")
        self.assertIn("code", w.get_owner({"peek": True}))

    def test_bare_code_with_spaces_works(self):
        w = self.wiz([])
        c = self.code(w)
        w.poll = lambda timeout: [msg(7, "  " + c + " ")]
        self.assertTrue(w.get_owner({})["ok"])

    def test_old_code_after_reset_rejected(self):
        w = self.wiz([])
        old = self.code(w)
        r = w.get_owner({"reset": True})
        self.assertNotEqual(r["code"], old)
        w.poll = lambda timeout: [msg(7, "/start " + old)]
        self.assertFalse(w.get_owner({})["ok"])
        self.assertIsNone(w.owner)

    def test_reset_clears_confirmed_owner(self):
        w = self.wiz([])
        c = self.code(w)
        w.poll = lambda timeout: [msg(7, c)]
        w.get_owner({})
        w.get_owner({"confirm": 7})
        r = w.get_owner({"reset": True})
        self.assertIsNone(w.owner)
        self.assertTrue(r["waiting"])

    def test_group_message_with_code_ignored(self):
        w = self.wiz([])
        c = self.code(w)
        w.poll = lambda timeout: [msg(7, "/start " + c, chat="group")]
        self.assertFalse(w.get_owner({})["ok"])
        self.assertIsNone(w.owner)


class GroupsAfterInstallTests(unittest.TestCase):
    def wiz(self):
        w = setup.Wizard()
        w.target, w.token, w.bot = object(), "1:x", {"username": "pagerbot", "name": "P"}
        return w

    def test_get_groups_before_install_still_polls(self):
        w = self.wiz()
        w.groups = {-5: "Team"}
        seen = []
        w.poll = lambda timeout: seen.append(timeout) or []
        self.assertEqual(w.get_groups({})["groups"], [{"id": -5, "title": "Team"}])
        self.assertEqual(seen, [0])

    def test_get_groups_after_install_does_not_poll(self):
        w = self.wiz()
        w.job = {"lines": [], "done": True, "ok": True, "error": ""}
        w.poll = lambda timeout: self.fail("polled")
        r = w.get_groups({})
        self.assertEqual(r, {"ok": False, "error": "installed"})


class PollLockTests(unittest.TestCase):
    def test_poll_updates_offset_and_groups_under_poll_lock(self):
        class Watch(setup.Wizard):
            def __setattr__(self, name, value):
                if name == "offset" and getattr(self, "_watch", False):
                    self._held.append(("offset", self.poll_lock.locked(), value))
                super().__setattr__(name, value)

        w = Watch()
        w._held = []
        w._watch = True
        w.target, w.token = object(), "1:x"

        class Groups(dict):
            def __setitem__(self, key, value):
                w._held.append(("groups", w.poll_lock.locked(), key))
                super().__setitem__(key, value)

        w.groups = Groups()

        def tg(method, params=None, timeout=60):
            p = params or {}
            if p.get("limit") == 1:  # ack of a non-empty batch; poll(0) also sends timeout 0
                w._held.append(("ack", w.poll_lock.locked(), p.get("offset")))
                return {"ok": True, "result": []}
            return {"ok": True, "result": [
                {"update_id": 3, "message": {"chat": {"id": -9, "type": "supergroup", "title": "Ops"}}},
                {"update_id": 7, "my_chat_member": {"chat": {"id": -8, "type": "group", "title": "Lab"}}},
            ]}

        w.tg = tg
        self.assertIsNot(w.poll_lock, w.lock)
        self.assertEqual([u["update_id"] for u in w.poll(0)], [3, 7])
        self.assertEqual(w.offset, 8)
        self.assertEqual(w.groups, {-9: "Ops", -8: "Lab"})
        self.assertTrue(w._held)
        self.assertTrue(all(locked for _kind, locked, *_rest in w._held))
        self.assertFalse(w.poll_lock.locked())

    def test_concurrent_polls_keep_the_highest_offset(self):
        w = setup.Wizard()
        w.target, w.token = object(), "1:x"
        mu = threading.Lock()
        seq = {"n": 0}

        def tg(method, params=None, timeout=60):
            p = params or {}
            if p.get("limit") == 1:  # the ack after a non-empty batch, not the poll itself
                return {"ok": True, "result": []}
            with mu:
                seq["n"] += 1
                uid = seq["n"]
            return {"ok": True, "result": [
                {"update_id": uid, "message": {"chat": {"id": uid, "type": "private"}, "text": "x"}}]}

        w.tg = tg
        threads = [threading.Thread(target=w.poll, args=(0,)) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
            self.assertFalse(t.is_alive())
        self.assertEqual(w.offset, 9)

    def test_get_owner_polls_outside_the_state_lock(self):
        w = setup.Wizard()
        w.target, w.token, w.bot = object(), "1:x", {"username": "pagerbot", "name": "P"}
        seen = {}

        def tg(method, params=None, timeout=60):
            if (params or {}).get("limit") == 1:
                return {"ok": True, "result": []}
            seen["state"] = w.lock.locked()
            seen["poll"] = w.poll_lock.locked()
            return {"ok": True, "result": []}

        w.tg = tg
        w.get_owner({"peek": True})
        r = w.get_owner({})
        self.assertTrue(r["waiting"])
        self.assertFalse(seen["state"])
        self.assertTrue(seen["poll"])


if __name__ == "__main__":
    unittest.main()
