#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""Unit tests for aurshare.py CLI (P14 argument validation) and shared assets."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
AURSHARE = ROOT / "aurshare.py"
sys.path.insert(0, str(ROOT))
import aurshare  # noqa: E402
import docinfra  # noqa: E402


def _seed_repo(repo, git_init=True):
    """Create canonical tracked assets and generated-asset inputs inside repo."""
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@localhost"]
    if git_init:
        subprocess.run(git + ["init", "-q", "--template=", str(repo)],
                       check=True, capture_output=True)

    paths = set()
    for name, rel in aurshare.SHARED_ASSETS.items():
        if name in aurshare.GENERATED_ASSETS:
            paths.update(aurshare.GENERATED_ASSETS[name]["inputs"])
        else:
            paths.add(rel)

    for rel in sorted(paths):
        p = repo / rel.rstrip("/")
        if rel.endswith("/"):
            p.mkdir(parents=True, exist_ok=True)
            target = p / "keep"
            target.write_text("ok\n")
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            target = p
            target.write_text("ok\n")
        if git_init:
            subprocess.run(["git", "-C", str(repo), "add", str(target)],
                           check=True, capture_output=True)

    if git_init:
        subprocess.run(git + ["-C", str(repo), "commit", "-q", "-m", "seed"],
                       check=True, capture_output=True)


def _write_fake_toolchain(bindir, fail_make=False):
    """Create executable stub `rustc` and `make` scripts in `bindir`."""
    bindir.mkdir(parents=True, exist_ok=True)
    rustc = bindir / "rustc"
    rustc.write_text("#!/bin/sh\nexit 0\n")
    rustc.chmod(0o755)

    make = bindir / "make"
    if fail_make:
        make.write_text("#!/bin/sh\necho 'synthetic rustc failure' >&2\nexit 2\n")
    else:
        make.write_text(
            "#!/bin/sh\n"
            "dir='.'\n"
            "if [ \"$1\" = '-C' ]; then dir=\"$2\"; fi\n"
            "case \"$dir\" in\n"
            "  */server|server) echo built-so > \"$dir/libaur.so\" ;;\n"
            "  */gate|gate) echo built-gate > \"$dir/aur-gate\" ;;\n"
            "esac\n"
            "exit 0\n"
        )
    make.chmod(0o755)


class AurshareCliTests(unittest.TestCase):
    def run_cli(self, *args, share_root=None, path_env=None):
        env = dict(os.environ)
        if share_root is not None:
            env["AUR_SHARE_ROOT"] = str(share_root)
        if path_env is not None:
            env["PATH"] = str(path_env)
        return subprocess.run(
            [sys.executable, "-I", str(AURSHARE), *args],
            env=env,
            capture_output=True,
            text=True,
        )

    def test_p14_help_and_bad_or_missing_arguments(self):
        for argv in (["-h"], ["--help"], ["devlink", "--help"], ["verify", "-h"]):
            r = self.run_cli(*argv)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("usage:", r.stdout)

        bad_invocations = (
            ["--bogus"],
            ["--help", "extra"],
            ["devlink", "--bogus"],
            ["devlink", "/nonexistent/aur-root"],
            ["devlink", str(ROOT), "no-such-asset.py"],
            ["install", "/nonexistent/proj"],
            ["link", "/nonexistent/proj"],
            ["discover", "/nonexistent/root"],
            ["verify", "extra"],
            ["list", "extra"],
            ["canonical", "a", "b"],
            ["deploy"],
            ["deploy", str(ROOT)],
            ["devlink"],
            ["install"],
            ["link"],
            ["register"],
            ["discover"],
        )
        for argv in bad_invocations:
            r = self.run_cli(*argv)
            self.assertEqual(r.returncode, 2, f"{argv} returned {r.returncode}: {r.stdout}{r.stderr}")
            self.assertIn("usage:", r.stderr, f"missing usage for {argv}")
            self.assertNotIn("Traceback", r.stderr, f"traceback leaked for {argv}")

    def test_canonical_checks_tracked_assets_and_worktree_inputs(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "aur"
            repo.mkdir()
            _seed_repo(repo)
            # Generated binaries are gitignored, so canonical passes without them.
            r_clean = self.run_cli("canonical", str(repo))
            self.assertEqual(r_clean.returncode, 0, r_clean.stdout + r_clean.stderr)

            # Linked worktree (`.git` is a file, not a directory) also passes.
            wt = Path(d) / "aur-wt"
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "add", "-q", "-b", "wt", str(wt)],
                check=True,
                capture_output=True,
            )
            r_wt = self.run_cli("canonical", str(wt))
            self.assertEqual(r_wt.returncode, 0, r_wt.stdout + r_wt.stderr)

            # Untracked shared asset -> non-zero canonical refusal.
            subprocess.run(["git", "-C", str(repo), "rm", "-q", "--cached", "tools/transcript.py"],
                           check=True, capture_output=True)
            r = self.run_cli("canonical", str(repo))
            self.assertEqual(r.returncode, 1)
            self.assertIn("UNTRACKED", r.stdout + r.stderr)

            # Untracked generated-asset input -> non-zero canonical refusal.
            subprocess.run(["git", "-C", str(repo), "add", "tools/transcript.py"],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "rm", "-q", "--cached", "server/libaur.rs"],
                           check=True, capture_output=True)
            r_in = self.run_cli("canonical", str(repo))
            self.assertEqual(r_in.returncode, 1)
            self.assertIn("UNTRACKED", r_in.stdout + r_in.stderr)
            self.assertIn("server/libaur.rs", r_in.stdout)

    def test_isolated_devlink_and_verify(self):
        with tempfile.TemporaryDirectory() as d:
            share = Path(d) / "share"
            self.assertEqual(self.run_cli("devlink", str(ROOT), share_root=share).returncode, 0)
            r = self.run_cli("verify", share_root=share)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("clean", r.stdout)

    def test_ensure_built_states_and_errors(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "aur"
            repo.mkdir()
            _seed_repo(repo, git_init=False)
            empty_bin = Path(d) / "empty-bin"
            empty_bin.mkdir()
            fake_bin = Path(d) / "fake-bin"
            _write_fake_toolchain(fake_bin)
            fail_bin = Path(d) / "fail-bin"
            _write_fake_toolchain(fail_bin, fail_make=True)
            share = Path(d) / "share"

            # Missing binary and missing rustc/make -> loud CLI exit 1.
            r_miss = self.run_cli("deploy", str(repo), "3.0.0",
                                  share_root=share, path_env=empty_bin)
            self.assertEqual(r_miss.returncode, 1)
            self.assertIn("rustc", r_miss.stderr)
            self.assertIn("libaur.so", r_miss.stderr)
            self.assertNotIn("Traceback", r_miss.stderr)
            self.assertFalse((share / "libaur.so").exists())

            # Partial devlink of a non-generated asset never invokes make/rustc.
            with mock.patch.dict(os.environ, {"PATH": str(empty_bin)}):
                self.assertEqual(aurshare.ensure_built(repo, names=["token-usage.py"]), [])
                self.assertEqual(aurshare.devlink(repo, names=["token-usage.py"],
                                                  log=lambda _m: None, share=share), 1)

            # Missing binary with make+rustc builds outputs and deploy stages them.
            r_dep = self.run_cli("deploy", str(repo), "3.0.0",
                                 share_root=share, path_env=fake_bin)
            self.assertEqual(r_dep.returncode, 0, r_dep.stderr)
            self.assertTrue((repo / "server" / "libaur.so").is_file())
            self.assertTrue((repo / "gate" / "aur-gate").is_file())
            self.assertTrue((share / "libaur.so.3.0.0").is_file())
            self.assertTrue((share / "aur-gate.3.0.0").is_file())

            # Up-to-date outputs require no rebuild (succeeds without rustc on PATH).
            with mock.patch.dict(os.environ, {"PATH": str(empty_bin)}):
                self.assertEqual(aurshare.ensure_built(repo), [])

            # Stale binary: gate-only source rebuilds only aur-gate; shared libaur.rs rebuilds both.
            gate_in = repo / "gate" / "src" / "main.rs"
            gate_out = repo / "gate" / "aur-gate"
            gate_ns = gate_out.stat().st_mtime_ns + 1_000_000_000
            os.utime(gate_in, ns=(gate_ns, gate_ns))
            with mock.patch.dict(os.environ, {"PATH": str(fake_bin)}):
                self.assertEqual(aurshare.ensure_built(repo, log=lambda _m: None), ["aur-gate"])

            inp = repo / "server" / "libaur.rs"
            out = repo / "server" / "libaur.so"
            newer_ns = max(out.stat().st_mtime_ns,
                           (repo / "gate" / "aur-gate").stat().st_mtime_ns) + 1_000_000_000
            os.utime(inp, ns=(newer_ns, newer_ns))
            with mock.patch.dict(os.environ, {"PATH": str(fake_bin)}):
                rebuilt = aurshare.ensure_built(repo, log=lambda _m: None)
            self.assertEqual(rebuilt, ["libaur.so", "aur-gate"])

            # Make failure surfaces compiler output cleanly and aborts build.
            newer_ns2 = (repo / "server" / "libaur.so").stat().st_mtime_ns + 1_000_000_000
            os.utime(inp, ns=(newer_ns2, newer_ns2))
            with mock.patch.dict(os.environ, {"PATH": str(fail_bin)}):
                with self.assertRaises(RuntimeError) as ctx:
                    aurshare.ensure_built(repo, names=["libaur.so"])
            self.assertIn("synthetic rustc failure", str(ctx.exception))

    def test_project_links_include_gen_diff_and_link_project(self):
        self.assertIn("gen-diff.sh", aurshare.PROJECT_LINKS)
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "aur"
            repo.mkdir()
            _seed_repo(repo, git_init=False)
            fake_bin = Path(d) / "fake-bin"
            _write_fake_toolchain(fake_bin)
            share = Path(d) / "share"
            proj = Path(d) / "proj"
            proj.mkdir()

            self.assertEqual(
                self.run_cli("deploy", str(repo), "3.0.0",
                             share_root=share, path_env=fake_bin).returncode,
                0,
            )
            r_link = self.run_cli("link", str(proj), share_root=share)
            self.assertEqual(r_link.returncode, 0, r_link.stderr)
            gd = proj / "aur" / "gen-diff.sh"
            self.assertTrue(gd.is_symlink())
            self.assertEqual(gd.readlink(), share / "gen-diff.sh")

    def test_docinfra_install_review_infra_builds_and_copies_libaur_and_doc_css(self):
        with tempfile.TemporaryDirectory() as d:
            here = Path(d) / "src"
            (here / "server").mkdir(parents=True)
            (here / "templates").mkdir(parents=True)
            shutil.copy2(ROOT / "aurshare.py", here / "aurshare.py")
            for name in ("server.py", "open-manifest.sh", "manifest-link.js",
                         "ann.css", "ann.js", "doc.css", "Makefile", "libaur.rs"):
                (here / "server" / name).write_text(f"# {name}\n")
            (here / "templates" / "manifest.html").write_text("<html></html>\n")

            proj = Path(d) / "proj"
            proj.mkdir()
            empty_bin = Path(d) / "empty-bin"
            empty_bin.mkdir()
            fake_bin = Path(d) / "fake-bin"
            _write_fake_toolchain(fake_bin)

            with mock.patch.object(docinfra, "resource_root", return_value=here):
                with mock.patch.dict(os.environ, {"PATH": str(empty_bin)}):
                    with self.assertRaises(RuntimeError) as ctx:
                        docinfra.install_review_infra(proj, False, log=lambda _m: None)
                self.assertIn("rustc", str(ctx.exception))

                logs = []
                with mock.patch.dict(os.environ, {"PATH": str(fake_bin)}):
                    docinfra.install_review_infra(proj, False, log=logs.append)

            self.assertTrue((proj / "aur" / "libaur.so").is_file())
            self.assertTrue((proj / "aur" / "server.py").is_file())
            self.assertTrue((proj / "aur-docs" / "doc.css").is_file())
            self.assertTrue((proj / "aur-docs" / "ann.css").is_file())


if __name__ == "__main__":
    unittest.main()
