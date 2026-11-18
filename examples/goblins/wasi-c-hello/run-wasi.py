"""Run the compiled WASI module with only the declared mounts and environment."""

import argparse

from wasmtime import ExitTrap, Linker, Module, Store, WasiConfig

parser = argparse.ArgumentParser()
parser.add_argument("command", choices=["run"])
parser.add_argument("--dir", action="append", default=[])
parser.add_argument("--env", action="append", default=[])
parser.add_argument("module")
args = parser.parse_args()

wasi = WasiConfig()
wasi.argv = [args.module]
wasi.env = [tuple(value.split("=", 1)) for value in args.env]
wasi.inherit_stdout()
wasi.inherit_stderr()
for directory in args.dir:
    wasi.preopen_dir(directory, directory)
store = Store()
store.set_wasi(wasi)
linker = Linker(store.engine)
linker.define_wasi()
module = Module.from_file(store.engine, args.module)
instance = linker.instantiate(store, module)
try:
    instance.exports(store)["_start"](store)
except ExitTrap as exit_status:
    raise SystemExit(exit_status.code) from None
