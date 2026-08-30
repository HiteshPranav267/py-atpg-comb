"""The dataset generator must read an upload in the format it actually is.

The API wrote every upload to a temp file named ".v" regardless of what was
uploaded, so a BENCH netlist was parsed by the Verilog parser. That parser maps
any unrecognised token to a BUF instead of failing, so the mismatch produced a
circuit wired out of the wrong tokens -- gates but no declared inputs or
outputs -- and a complete, downloadable dataset of meaningless netlists. The
failure only surfaced much later, as an ATPG run that emitted no results
summary.
"""
import json
import os

import pytest

from pyatpg.api.services.trojan_service import _suffix_for
from pyatpg.core.netlist import NetlistParser
from pyatpg.core.verilog_parser import VerilogParser
from pyatpg.trojan.generator import TrojanDatasetGenerator
from pyatpg.trojan.inserter import TrojanInserter
from pyatpg.trojan.strategy import RandomStrategy

BENCH_SOURCE = open("benchmarks/c432.bench").read()


def _generator(tmp_path) -> TrojanDatasetGenerator:
    return TrojanDatasetGenerator(
        inserter=TrojanInserter(strategy=RandomStrategy()),
        output_dir=str(tmp_path / "datasets"),
    )


def test_bench_text_read_as_verilog_yields_no_ports():
    """The premise of the bug: the mismatch is silent, not an exception."""
    mangled = VerilogParser().parse(BENCH_SOURCE)
    assert mangled.gates, "the Verilog parser invents gates from BENCH tokens"
    assert not mangled.inputs
    assert not mangled.outputs


def test_suffix_follows_the_upload():
    assert _suffix_for("c432.bench") == ".bench"
    assert _suffix_for("c432.v") == ".v"
    assert _suffix_for("c432.verilog") == ".verilog"
    assert _suffix_for("C432.BENCH") == ".bench", "extension match is case-insensitive"


def test_suffix_falls_back_to_verilog():
    for filename in (None, "", "circuit", "circuit.txt"):
        assert _suffix_for(filename) == ".v"


def test_bench_named_as_verilog_is_rejected(tmp_path):
    """The mismatch must fail loudly instead of generating a garbage dataset."""
    path = tmp_path / "c432.v"
    path.write_text(BENCH_SOURCE)

    with pytest.raises(ValueError, match="do not look like"):
        _generator(tmp_path).generate(circuit_path=str(path), variants=2)


def test_bench_named_correctly_generates(tmp_path):
    path = tmp_path / "c432.bench"
    path.write_text(BENCH_SOURCE)

    generated = _generator(tmp_path).generate(circuit_path=str(path), variants=3)

    assert generated == 3
    ds = tmp_path / "datasets" / "c432"
    clean = NetlistParser().parse((ds / "clean" / "circuit.bench").read_text())
    assert clean.inputs and clean.outputs and clean.gates

    for i in range(1, 4):
        variant = ds / f"trojan_{i:04d}"
        trojan = NetlistParser().parse((variant / "circuit.bench").read_text())
        # The trojan must be a real addition to a circuit that still parses.
        assert len(trojan.gates) > len(clean.gates)
        assert trojan.inputs == clean.inputs
        meta = json.loads((variant / "metadata.json").read_text())
        assert meta["circuit"] == "c432"


def test_unsupported_extension_names_the_file(tmp_path):
    path = tmp_path / "c432.txt"
    path.write_text(BENCH_SOURCE)

    with pytest.raises(ValueError, match="Unsupported base circuit format"):
        _generator(tmp_path).generate(circuit_path=str(path), variants=1)


def test_crlf_upload_parses_identically_to_lf(tmp_path):
    """A CRLF upload must not silently lose ports.

    The service wrote uploads in default text mode, so on Windows a CRLF file
    landed on disk as "\\r\\r\\n"; the surviving carriage return broke the
    Verilog parser's continuation-line joining and c432's 36 declared inputs
    parsed as 10 -- no error, just a quietly wrong dataset.
    """
    from pyatpg.api.services.trojan_service import TrojanService

    verilog = open("benchmarks/c432.v", encoding="utf-8").read()
    crlf = verilog.replace("\r\n", "\n").replace("\n", "\r\n")

    for label, text in (("lf", crlf.replace("\r\n", "\n")), ("crlf", crlf)):
        out = tmp_path / label
        TrojanService.generate_dataset(
            source_text=text,
            circuit_name="c432",
            variants=1,
            filename="c432.v",
            output_dir=str(out),
        )
        parsed = NetlistParser().parse(
            (out / "c432" / "clean" / "circuit.bench").read_text(encoding="utf-8")
        )
        assert len(parsed.inputs) == 36, f"{label} upload lost primary inputs"
        assert len(parsed.outputs) == 7


def test_exported_netlists_use_unix_line_endings(tmp_path):
    """Atalanta's Linux build rejects a CRLF netlist token by token."""
    path = tmp_path / "c432.bench"
    path.write_text(BENCH_SOURCE, encoding="utf-8")
    _generator(tmp_path).generate(circuit_path=str(path), variants=1)

    ds = tmp_path / "datasets" / "c432"
    for produced in (
        ds / "clean" / "circuit.bench",
        ds / "clean" / "circuit.v",
        ds / "trojan_0001" / "circuit.bench",
        ds / "trojan_0001" / "circuit.v",
    ):
        assert b"\r" not in produced.read_bytes(), f"{produced.name} has CR"


def test_skipped_variants_are_not_counted_or_left_behind(tmp_path):
    """A variant that cannot be inserted must not be reported or shipped.

    c17 has six gates, so RandomStrategy regularly finds no acyclic payload
    target and the variant is skipped. The count must reflect what was written,
    and no empty directory may be left for the zip to pick up.
    """
    path = tmp_path / "c17.bench"
    path.write_text(open("benchmarks/c17.bench").read())

    generated = _generator(tmp_path).generate(circuit_path=str(path), variants=25)

    ds = tmp_path / "datasets" / "c17"
    written = [d for d in os.listdir(ds) if d.startswith("trojan_")]
    assert generated == len(written)
    for name in written:
        assert (ds / name / "circuit.bench").is_file(), f"{name} is an empty shell"
