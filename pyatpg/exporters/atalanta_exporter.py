import os
import zipfile
import shutil
import platform


class AtlantaExporter:
    """Generates Atalanta ATPG job bundles for combinational circuits."""

    def __init__(self, circuit_name='circuit', atalanta_bin=None):
        self.circuit_name = circuit_name
        self.atalanta_bin = atalanta_bin

        self._bench_netlist = ''
        self._fault_list = ''
        self._bash_script = ''
        self._source_name = ''

    def generate_scripts(
        self,
        circuit_source,
        flagged_gates,
        circuit=None,
        source_name="circuit.v"
    ):
        """Generate Atalanta job script, .bench representation,
        and targeted fault list.
        """
        self._source_name = source_name

        # Convert to bench format if needed
        if source_name.endswith(('.v', '.verilog')):
            if not circuit:
                from pyatpg.core.verilog_parser import VerilogParser
                circuit = VerilogParser().parse(circuit_source)

            self._bench_netlist = circuit.to_bench()
        else:
            self._bench_netlist = circuit_source

        # Format targeted faults
        self._fault_list = "\n".join(
            [
                g['gate_id'] + " /0\n" + g['gate_id'] + " /1"
                for g in flagged_gates
            ]
        )

        # Generate platform-appropriate script. Both variants abort on a failed
        # Atalanta invocation -- without that, a crashed run leaves empty logs
        # that the parser happily reads as 0% coverage.
        if platform.system() == "Windows":
            self._bash_script = f"""@echo off
setlocal

echo [PyATPG] Starting Atalanta Job for {self.circuit_name}

REM %~dp0 is this script's own directory -- Windows won't resolve a bare
REM Atalanta.exe from the working directory.
cd /d "%~dp0"

REM First generate a baseline
"%~dp0Atalanta.exe" -t {self.circuit_name}_baseline.vec {self.circuit_name}.bench > baseline_atalanta.log
if errorlevel 1 exit /b 1

REM Now run targeted faults
"%~dp0Atalanta.exe" -f {self.circuit_name}.faults -t {self.circuit_name}.vec -U {self.circuit_name}.ufaults {self.circuit_name}.bench > targeted_atalanta.log
if errorlevel 1 exit /b 1

echo [PyATPG] Done.
"""
        else:
            self._bash_script = f"""#!/bin/bash
set -e

echo "[PyATPG] Starting Atalanta Job for {self.circuit_name}"

chmod +x ./atalanta

# First generate a baseline
./atalanta -t {self.circuit_name}_baseline.vec {self.circuit_name}.bench > baseline_atalanta.log

# Now run targeted faults
./atalanta -f {self.circuit_name}.faults -t {self.circuit_name}.vec -U {self.circuit_name}.ufaults {self.circuit_name}.bench > targeted_atalanta.log

echo "[PyATPG] Done."
"""

    def export_bundle(self, output_dir):
        """Package everything into a zip file along with Atalanta binary."""

        os.makedirs(output_dir, exist_ok=True)

        zip_path = os.path.join(
            output_dir,
            f"{self.circuit_name}_atalanta_job.zip"
        )

        # Find project root
        project_root = os.path.dirname(
            os.path.dirname(
                os.path.dirname(
                    os.path.abspath(__file__)
                )
            )
        )

        # Allow caller to provide a custom Atalanta binary
        if self.atalanta_bin:
            atalanta_bin = self.atalanta_bin
        elif platform.system() == "Windows":
            atalanta_bin = os.path.join(
                project_root,
                "Atalanta",
                "data",
                "Atalanta.exe"
            )
        else:
            atalanta_bin = os.path.join(
                project_root,
                "Atalanta",
                "atalanta"
            )

        with zipfile.ZipFile(zip_path, 'w') as zf:
            zf.writestr(
                f"{self.circuit_name}.bench",
                self._bench_netlist
            )

            zf.writestr(
                f"{self.circuit_name}.faults",
                self._fault_list
            )

            if platform.system() == "Windows":
                zf.writestr(
                    "run_atalanta.bat",
                    self._bash_script
                )
            else:
                zf.writestr(
                    "run_atalanta.sh",
                    self._bash_script
                )

            if os.path.exists(atalanta_bin):
                if platform.system() == "Windows":
                    zf.write(
                        atalanta_bin,
                        "Atalanta.exe"
                    )
                else:
                    zf.write(
                        atalanta_bin,
                        "atalanta"
                    )
            else:
                print(
                    f"WARNING: Atalanta binary not found at {atalanta_bin}"
                )

        return zip_path