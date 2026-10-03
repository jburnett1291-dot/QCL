import ast
import json
import unittest
from pathlib import Path
from types import SimpleNamespace


APP_PATH = Path(__file__).with_name("app.py")

EXPECTED_PAGES = [
    "📰 News & Updates",
    "📱 Mobile Hub",
    "📝 Register",
    "🏠 League Home & Awards",
    "Film Terminal",
    "🌌 Player Galaxy",
    "🏅 Awards & Rewards",
    "🏆 Power Rankings & SOS",
    "🏢 Franchise Hub",
    "🛡️ League Teams",
    "🔦 Player Spotlight",
    "🗃️ Full Player Database",
    "⚔️ Head-to-Head Radar",
    "🧪 Lineup Lab",
    "🥊 Rivalry Corner",
    "🏆 Playoffs",
    "🔮 Oracle Predictor",
    "🔬 Advanced Analytics Lab",
    "🏦 The Vault",
    "📈 Card Market",
    "🎴 Qwiks TCG",
    "🎮 QTCG Desk",
    "🧢 Draft Room",
    "🗂 My QTCG Binder",
    "👤 My Profile",
    "🎁 Open Packs",
    "🃏 Player Cards",
    "💬 Discord",
    "📖 Record Book & Milestones",
]

ROLE_PAGES = {
    "🏢 GM Desk": "_viewer_access['role'] == 'gm'",
    "👥 Players Desk": "_viewer_access['role'] == 'player'",
    "Commissioner Desk": "_commissioner_access",
}


def _view_list(tree):
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "VIEWS"
                   for target in node.targets):
            continue
        if not isinstance(node.value, ast.List):
            break
        return [
            item.value for item in node.value.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        ]
    raise AssertionError("app.py must define VIEWS as a literal list")


def _view_mode_dispatch_labels(tree):
    labels = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not isinstance(node.left, ast.Name) or node.left.id != "view_mode":
            continue
        for operator, comparator in zip(node.ops, node.comparators):
            if not isinstance(operator, (ast.Eq, ast.In)):
                continue
            candidates = (
                comparator.elts
                if isinstance(comparator, (ast.List, ast.Tuple, ast.Set))
                else [comparator]
            )
            labels.update(
                item.value for item in candidates
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            )
    return labels


def _inserted_page_labels(node):
    labels = []
    for statement in node.body:
        child = statement.value if isinstance(statement, ast.Expr) else statement
        if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
            continue
        if not (
            isinstance(child.func.value, ast.Name)
            and child.func.value.id == "VIEWS"
            and child.func.attr == "insert"
            and child.args
        ):
            continue
        label = child.args[-1]
        if isinstance(label, ast.Constant) and isinstance(label.value, str):
            labels.append(label.value)
    return labels



def _role_navigation_branch(tree):
    for node in tree.body:
        test = getattr(node, "test", None)
        if (
            isinstance(node, ast.If)
            and isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Subscript)
            and isinstance(test.left.value, ast.Name)
            and test.left.value.id == "_viewer_access"
        ):
            return node
    raise AssertionError("app.py must define role-specific navigation")


def _commissioner_navigation_branch(tree):
    for node in tree.body:
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "_commissioner_access"
        ):
            return node
    raise AssertionError("app.py must define commissioner navigation")


def _build_navigation(tree, role="", commissioner_access=False):
    namespace = {
        "VIEWS": list(_view_list(tree)),
        "_viewer_access": {"role": role},
        "_commissioner_access": commissioner_access,
    }
    branches = ast.Module(
        body=[_role_navigation_branch(tree), _commissioner_navigation_branch(tree)],
        type_ignores=[],
    )
    exec(compile(branches, str(APP_PATH), "exec"), namespace)
    return namespace["VIEWS"]

class QclNavigationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(APP_PATH.read_text(encoding="utf-8"), filename=str(APP_PATH))
        cls.pages = _view_list(cls.tree)

    def test_all_existing_pages_remain_in_default_navigation(self):
        self.assertEqual(self.pages, EXPECTED_PAGES)

    def test_every_default_page_has_a_render_dispatch(self):
        self.assertTrue(
            set(self.pages).issubset(_view_mode_dispatch_labels(self.tree)),
            "Every navigation page must retain its page-render dispatch.",
        )

    def test_role_specific_pages_are_added_only_under_their_access_checks(self):
        conditional_pages = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.If):
                condition = ast.unparse(node.test)
                for page in _inserted_page_labels(node):
                    conditional_pages.setdefault(page, []).append(condition)

        for page, required_condition in ROLE_PAGES.items():
            with self.subTest(page=page):
                self.assertNotIn(page, self.pages)
                self.assertIn(required_condition, conditional_pages.get(page, []))
                self.assertIn(page, _view_mode_dispatch_labels(self.tree))


    def test_role_navigation_keeps_existing_pages_for_every_viewer(self):
        default_views = _build_navigation(self.tree)
        player_views = _build_navigation(self.tree, role="player")
        gm_views = _build_navigation(self.tree, role="gm")

        self.assertNotIn("Commissioner Desk", default_views)
        self.assertNotIn("🏢 GM Desk", default_views)
        self.assertNotIn("👥 Players Desk", default_views)
        self.assertIn("👥 Players Desk", player_views)
        self.assertNotIn("🏢 GM Desk", player_views)
        self.assertIn("🏢 GM Desk", gm_views)
        self.assertNotIn("👥 Players Desk", gm_views)
        for views in (default_views, player_views, gm_views):
            retained = [page for page in views if page not in ROLE_PAGES]
            self.assertEqual(retained, EXPECTED_PAGES)

    def test_commissioner_page_is_added_only_for_verified_access(self):
        denied = _build_navigation(
            self.tree, role="player", commissioner_access=False
        )
        allowed = _build_navigation(
            self.tree, role="player", commissioner_access=True
        )

        self.assertNotIn("Commissioner Desk", denied)
        self.assertEqual(allowed.count("Commissioner Desk"), 1)
        self.assertIn("👥 Players Desk", allowed)
        self.assertEqual(
            [page for page in allowed if page not in ROLE_PAGES],
            EXPECTED_PAGES,
        )

    def test_commissioner_visibility_uses_server_verified_status(self):
        assignment_found = any(
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Tuple)
            and [item.id for item in node.targets[0].elts if isinstance(item, ast.Name)]
            == ["_commissioner_access", "_commissioner_access_error"]
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "_commissioner_status_for"
            and len(node.value.args) == 1
            and isinstance(node.value.args[0], ast.Name)
            and node.value.args[0].id == "_viewer"
            for node in ast.walk(self.tree)
        )
        self.assertTrue(
            assignment_found,
            "Commissioner navigation must use server-verified access status.",
        )


def _function_named(tree, name):
    return next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )


class QclActivitySessionBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(APP_PATH))
        cls.bridge = _function_named(cls.tree, "_publish_activity_session")
        cls.closed_season = _function_named(cls.tree, "_closed_season_desk")

    def _run_function(self, function, namespace):
        module = ast.Module(body=[function], type_ignores=[])
        exec(compile(module, str(APP_PATH), "exec"), namespace)

    def test_verified_user_receives_a_signed_session_in_same_origin_storage(self):
        writes = []
        components = SimpleNamespace(
            html=lambda markup, **kwargs: writes.append((markup, kwargs))
        )
        namespace = {
            "components": components,
            "json": json,
            "st": SimpleNamespace(session_state={}),
            "_qtcg_session_token": lambda user: "signed-session",
        }
        self._run_function(self.bridge, namespace)

        namespace["_publish_activity_session"]({"id": "123"})
        self.assertEqual(len(writes), 1)
        markup, kwargs = writes[0]
        self.assertIn("window.parent.localStorage", markup)
        self.assertIn('storage.setItem("qcl-session", session)', markup)
        self.assertIn(json.dumps("signed-session"), markup)
        self.assertEqual(kwargs["height"], 0)

    def test_anonymous_page_does_not_erase_an_existing_activity_session(self):
        writes = []
        namespace = {
            "components": SimpleNamespace(
                html=lambda markup, **kwargs: writes.append(markup)
            ),
            "json": json,
            "st": SimpleNamespace(session_state={}),
            "_qtcg_session_token": lambda user: self.fail("anonymous users get no token"),
        }
        self._run_function(self.bridge, namespace)

        namespace["_publish_activity_session"](None)
        self.assertEqual(writes, [])

    def test_explicit_sign_out_clears_the_shared_activity_session(self):
        writes = []
        state = {"_qtcg_activity_session_clear": True}
        namespace = {
            "components": SimpleNamespace(
                html=lambda markup, **kwargs: writes.append(markup)
            ),
            "json": json,
            "st": SimpleNamespace(session_state=state),
            "_qtcg_session_token": lambda user: self.fail("sign-out must not mint a token"),
        }
        self._run_function(self.bridge, namespace)

        namespace["_publish_activity_session"](None)
        self.assertEqual(len(writes), 1)
        self.assertIn('localStorage.removeItem("qcl-session")', writes[0])
        self.assertNotIn("_qtcg_activity_session_clear", state)

    def test_expired_and_logged_out_streamlit_sessions_schedule_activity_cleanup(self):
        self.assertIn(
            'st.session_state["_qtcg_activity_session_clear"] = True',
            self.source,
        )
        restore = _function_named(self.tree, "restore_session")
        login = _function_named(self.tree, "login_widget")
        expired_branch = next(
            node for node in ast.walk(restore)
            if isinstance(node, ast.If)
            and "auth_expires_at" in ast.unparse(node.test)
        )
        logout_branch = next(
            node for node in ast.walk(login)
            if isinstance(node, ast.If)
            and "Log out" in ast.unparse(node.test)
        )
        for branch in (expired_branch, logout_branch):
            self.assertTrue(
                any(
                    isinstance(node, ast.Assign)
                    and "_qtcg_activity_session_clear" in ast.unparse(node.targets)
                    for node in ast.walk(branch)
                )
            )

    def test_no_stats_screen_explains_coming_soon_and_keeps_member_access(self):
        calls = []
        namespace = {
            "st": SimpleNamespace(
                warning=lambda message: calls.append(("warning", message)),
                info=lambda message: calls.append(("info", message)),
                caption=lambda message: calls.append(("caption", message)),
            ),
            "_access": lambda user: {"role": "guest"},
            "current_user": lambda: None,
            "_gm_desk": lambda access: calls.append(("desk", access)),
            "login_widget": lambda **kwargs: calls.append(("login", kwargs)),
        }
        self._run_function(self.closed_season, namespace)

        namespace["_closed_season_desk"]("No games have been posted yet.")
        self.assertIn(("warning", "No games have been posted yet."), calls)
        coming_soon = next(message for kind, message in calls if kind == "info")
        self.assertIn("Coming soon", coming_soon)
        self.assertIn("Public pages, registration", coming_soon)
        self.assertIn(("login", {"key": "empty_season"}), calls)

    def test_registered_member_keeps_their_desk_when_no_stats_are_posted(self):
        calls = []
        access = {"role": "gm", "team_records": [{"team_name": "Example"}]}
        namespace = {
            "st": SimpleNamespace(
                warning=lambda message: calls.append(("warning", message)),
                info=lambda message: calls.append(("info", message)),
                caption=lambda message: calls.append(("caption", message)),
            ),
            "_access": lambda user: access,
            "current_user": lambda: {"id": "123"},
            "_gm_desk": lambda member_access: calls.append(("desk", member_access)),
            "login_widget": lambda **kwargs: calls.append(("login", kwargs)),
        }
        self._run_function(self.closed_season, namespace)

        namespace["_closed_season_desk"]("No games have been posted yet.")
        self.assertIn(("desk", access), calls)
        self.assertFalse(any(kind == "login" for kind, _ in calls))


if __name__ == "__main__":
    unittest.main()