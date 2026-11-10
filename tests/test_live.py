"""Live checks against visayanelectric.com: two requests. Run with `pytest -m live`."""

from __future__ import annotations

import pytest

from outages.config import SITEMAP_URL
from outages.discover import parse_sitemap
from outages.extract import extract_post
from outages.fetch import Fetcher
from outages.parse import parse_post

pytestmark = pytest.mark.live


def test_live_newest_post_parses():
    f = Fetcher(delay_s=(5, 6))
    try:
        xml = f.get(SITEMAP_URL)
        assert xml is not None
        refs = parse_sitemap(xml)
        assert len(refs) >= 151
        html = f.get(refs[-1].url)
        assert html is not None
    finally:
        f.close()
    result = parse_post(extract_post(html), refs[-1].slug)
    assert result.error is None, f"{refs[-1].slug}: {result.error}"
    assert result.outages
