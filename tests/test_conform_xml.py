"""FCPXML and XMEML conform parsing."""

from __future__ import annotations

from pathlib import Path

from matchref.conform_xml import parse_conform_xml

_FCPXML = """<?xml version="1.0"?>
<fcpxml version="1.9">
  <resources>
    <asset id="r1" name="SHOT_A"/>
    <asset id="r2" name="SHOT_B"/>
  </resources>
  <library>
    <event>
      <project>
        <sequence name="MySeq">
          <spine>
            <asset-clip ref="r1" name="ClipA" offset="0s" duration="4s" start="0s"/>
            <asset-clip ref="r2" name="ClipB" offset="0s" duration="5s" start="0s"/>
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""

_XMEML = """<?xml version="1.0"?>
<xmeml version="5">
  <sequence>
    <rate><timebase>24</timebase><ntsc>FALSE</ntsc></rate>
    <name>Seq</name>
    <media><video><track>
      <clipitem>
        <name>ShotX</name>
        <in>10</in>
        <out>110</out>
        <start>0</start>
        <file><pathurl>file:///media/ShotX.mov</pathurl></file>
      </clipitem>
    </track></video></media>
  </sequence>
</xmeml>
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_fcpxml_events_and_record_cursor(tmp_path: Path) -> None:
    res = parse_conform_xml(_write(tmp_path, "seq.fcpxml", _FCPXML), default_fps=24.0)
    assert len(res.events) == 2
    a, b = res.events
    assert a.clip_name == "ClipA"
    assert a.reel == "SHOT_A"  # resolved from the asset name
    assert (a.rec_in, a.rec_out) == (0, 96)  # 4s @ 24
    assert (b.rec_in, b.rec_out) == (96, 216)  # back-to-back, 5s @ 24
    assert b.reel == "SHOT_B"


def test_xmeml_clip_fields(tmp_path: Path) -> None:
    res = parse_conform_xml(_write(tmp_path, "seq.xml", _XMEML), default_fps=30.0)
    assert res.timecode_format.fps == 24.0  # from <rate><timebase>
    assert len(res.events) == 1
    e = res.events[0]
    assert e.clip_name == "ShotX"
    assert e.reel == "ShotX"
    assert (e.src_in, e.src_out) == (10, 110)
    assert (e.rec_in, e.rec_out) == (0, 100)
    assert e.source_file == "file:///media/ShotX.mov"


def test_dispatch_picks_parser_by_root(tmp_path: Path) -> None:
    # fcpxml root → FCPXML parser (asset-name reels); xmeml root → XMEML parser.
    fc = parse_conform_xml(_write(tmp_path, "a.fcpxml", _FCPXML))
    xm = parse_conform_xml(_write(tmp_path, "b.xml", _XMEML))
    assert fc.events[0].reel == "SHOT_A"
    assert xm.events[0].source_file.endswith("ShotX.mov")
