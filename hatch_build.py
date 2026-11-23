"""
The wheel's build hook (hatchling): adds what isn't in server/rates_terminal itself.

  dist/                               -> rates_terminal/web/        the built front ends (npm run build)
  vendor/python/*.whl (their code)    -> dh_chart_ext/, deephaven/plot/tradingview_lightweight/
  vendor/python/*.whl (licenses)      -> rates_terminal/licenses/<distribution>/
"""
import pathlib
import shutil
import zipfile

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name != "wheel":
            return
        root = pathlib.Path(self.root)
        dist = root / "dist"
        if not (dist / "index.html").is_file():
            raise RuntimeError("dist/ has no built front ends: run `npm run build` first")
        include = build_data["force_include"]
        include[str(dist)] = "rates_terminal/web"

        out = root / "build" / "vendored"
        shutil.rmtree(out, ignore_errors=True)
        code, licenses = out / "code", out / "licenses"
        for whl in sorted((root / "vendor" / "python").glob("*.whl")):
            dist_name = whl.name.split("-")[0]
            with zipfile.ZipFile(whl) as z:
                for name in z.namelist():
                    if name.endswith("/"):
                        continue
                    if ".dist-info/" in name:
                        if "/licenses/" in name:
                            target = licenses / dist_name / name.split("/licenses/", 1)[1]
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(z.read(name))
                        continue
                    z.extract(name, code)
        for top in sorted(code.iterdir()):
            include[str(top)] = top.name
        if licenses.is_dir():
            include[str(licenses)] = "rates_terminal/licenses"
