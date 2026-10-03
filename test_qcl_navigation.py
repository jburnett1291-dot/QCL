import ast
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()