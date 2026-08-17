"""Tests for AdapterRegistry."""

import pytest

from web_watcher.adapters import AdapterRegistry
from web_watcher.targets import WatchTarget


def _mk_target(**overrides):
    defaults = {
        "key": "github:example/project",
        "target_type": "github_repository",
        "name": "Example Project",
        "locator": "example/project",
    }
    defaults.update(overrides)
    return WatchTarget(**defaults)


class _GitHubAdapter:
    def supports(self, target):
        return target.target_type == "github_repository"

    def fetch(self, request):
        raise NotImplementedError


class _WebsiteAdapter:
    def supports(self, target):
        return target.target_type == "official_website"

    def fetch(self, request):
        raise NotImplementedError


class TestAdapterRegistry:

    def test_default_registry_is_empty(self):
        reg = AdapterRegistry()
        assert reg.adapters == ()

    def test_registry_holds_given_adapters(self):
        reg = AdapterRegistry([_GitHubAdapter(), _WebsiteAdapter()])
        assert len(reg.adapters) == 2

    def test_resolves_correct_adapter(self):
        reg = AdapterRegistry([_GitHubAdapter(), _WebsiteAdapter()])
        adapter = reg.resolve(_mk_target())
        assert isinstance(adapter, _GitHubAdapter)

    def test_resolve_raises_when_no_match(self):
        reg = AdapterRegistry([_WebsiteAdapter()])
        with pytest.raises(LookupError):
            reg.resolve(_mk_target())

    def test_resolve_raises_when_multiple_match(self):
        class _Wildcard:
            def supports(self, target):
                return True

            def fetch(self, request):
                raise NotImplementedError

        reg = AdapterRegistry([_GitHubAdapter(), _Wildcard()])
        with pytest.raises(LookupError, match="multiple"):
            reg.resolve(_mk_target())

    def test_empty_registry_raises(self):
        reg = AdapterRegistry()
        with pytest.raises(LookupError):
            reg.resolve(_mk_target())

    def test_adapters_tuple_immutable(self):
        reg = AdapterRegistry([_GitHubAdapter()])
        with pytest.raises(Exception):
            reg.adapters += (_WebsiteAdapter(),)
