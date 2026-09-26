from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.tags import (
    _parse_tag_file_mapping,
    load_mod_tags,
    load_vanilla_tags,
    resolve_country_filename,
    sort_generated_country_tags,
)


@pytest.mark.parametrize('suffix', ['', ' # existing country', ' # "existing country"'])
@pytest.mark.parametrize('filename', ['Sichuan.txt', 'Si#chuan.txt'])
@pytest.mark.parametrize('source', ['game', 'base_mod'])
def test_inherited_commented_registration(tmp_path, monkeypatch, suffix, filename, source):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    inherited = tmp_path / source
    tags = inherited / 'common/country_tags/00_countries.txt'
    tags.parent.mkdir(parents=True)
    original = f'# IGN = "countries/Ignored.txt"\nSIC = "countries/{filename}"{suffix}\n'
    tags.write_text(original, encoding='utf-8')
    definition = inherited / 'common/countries' / filename
    definition.parent.mkdir(parents=True)
    definition.write_text('color = { 1 2 3 }\n', encoding='utf-8')

    assert _parse_tag_file_mapping(tags.parent) == {'SIC': f'countries/{filename}'}
    assert load_vanilla_tags(inherited) == {'SIC'}
    assert load_mod_tags(inherited) == ['SIC']
    assert resolve_country_filename(inherited, 'SIC') == definition
    kwargs = {'hoi4_install': inherited} if source == 'game' else {'base_mod_paths': [inherited]}
    mod = Mod(tmp_path / 'mod', **kwargs)
    assert not mod.is_country_tag_available('SIC')
    assert mod.is_country_tag_available('IGN')
    assert mod.suggest_tag('Sicily') != 'SIC'
    assert 'SIC' not in mod.suggest_tags('Sicily')
    # Loading a country caches it; inspect resolution independently of creation.
    assert Mod(tmp_path / 'mod', **kwargs).get_country('SIC').color == (1, 2, 3)
    with pytest.raises(ValueError, match='allow_vanilla_override'):
        mod.create_country('SIC', 'Sicily')
    assert mod.preview() == ''
    assert mod.create_country('SIC', 'Sicily', allow_vanilla_override=True).tag == 'SIC'
    assert tags.read_text(encoding='utf-8') == original
    assert definition.read_text(encoding='utf-8') == 'color = { 1 2 3 }\n'


def test_readers_preserve_duplicate_precedence_and_sources(tmp_path):
    tags = tmp_path / 'common/country_tags'
    tags.mkdir(parents=True)
    first = 'SIC = "countries/First.txt" # "first"\n'
    second = 'SIC = "countries/Second.txt" # second\n'
    last = 'SIC = "countries/Last.txt" # last\n'
    (tags / '00_first.txt').write_text(first + second, encoding='utf-8')
    (tags / '99_last.txt').write_text(last, encoding='utf-8')
    assert _parse_tag_file_mapping(tags) == {'SIC': 'countries/Last.txt'}
    assert load_mod_tags(tmp_path) == ['SIC']
    mod = Mod(tmp_path)
    assert mod._country_tag_mappings[mod.mod_root] == {'SIC': 'countries/First.txt'}
    assert any(issue.code == 'duplicate_country_tag' for issue in mod.validate())
    assert (tags / '00_first.txt').read_text(encoding='utf-8') == first + second
    assert (tags / '99_last.txt').read_text(encoding='utf-8') == last


def test_generated_sorter_retains_existing_comment_and_duplicate_policy():
    text = (
        '# header\n'
        'ZZZ = "countries/First.txt" # first\n'
        'ZZZ = "countries/Second.txt" # second\n'
        'BBB = "countries/Old.txt"\n'
        'AAA = "countries/A.txt"\n'
        'BBB = "countries/New.txt"\n'
    )
    assert sort_generated_country_tags(text) == (
        '# header\n'
        'ZZZ = "countries/First.txt" # first\n'
        'ZZZ = "countries/Second.txt" # second\n'
        'AAA = "countries/A.txt"\n'
        'BBB = "countries/New.txt"\n'
    )
