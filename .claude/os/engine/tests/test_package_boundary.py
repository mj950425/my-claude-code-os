#!/usr/bin/env python3
"""패키지 경계를 자동으로 지킨다.

이 OS의 합격 기준은 하나다 — 속성 패키지를 통째로 지워도 엔진이 그대로 돈다.
사람이 매번 확인할 수 없으므로 여기서 기계가 확인한다.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _find_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".claude").is_dir():
            return parent
    raise RuntimeError("프로젝트 루트를 찾지 못했습니다.")


PROJECT_ROOT = _find_project_root()
OS_ROOT = PROJECT_ROOT / ".claude/os"
ENGINE_SCRIPTS = OS_ROOT / "engine/scripts"
DECLARED_LEAKS = OS_ROOT / "engine/contracts/declared-leaks.json"

# 엔진이 알아서는 안 되는 어휘. 특정 속성의 이름·라벨·데이터 출처가 여기 들어온다.
FORBIDDEN = ("가방", "성별", "남성", "여성", "29CM", "MALE", "FEMALE", "UNISEX", "productGender", "bag-category-gender")


ENGINE_WORKFLOWS = OS_ROOT / "engine/workflows"


def engine_sources() -> list[Path]:
    # 워크플로우도 엔진 코드다. 워크플로우가 과제의 어휘를 알면 새 과제마다 스크립트를 고쳐야 한다.
    return sorted(
        [
            path
            for pattern in ("**/*.py", "**/*.sh")
            for path in ENGINE_SCRIPTS.glob(pattern)
            if "__pycache__" not in path.parts
        ]
        + list(ENGINE_WORKFLOWS.glob("*.js"))
    )


def declared() -> dict[str, set[str]]:
    value = json.loads(DECLARED_LEAKS.read_text(encoding="utf-8"))
    return {item["file"]: set(item["terms"]) for item in value["leaks"]}


class EnginePurityTest(unittest.TestCase):
    """엔진 코드에는 도메인 어휘가 없어야 한다. 남은 것은 전부 선언되어야 한다."""

    def test_no_undeclared_domain_vocabulary(self) -> None:
        allowlist = declared()
        undeclared: list[str] = []
        for path in engine_sources():
            key = str(path.relative_to(OS_ROOT))
            body = path.read_text(encoding="utf-8")
            allowed = allowlist.get(key, set())
            for term in FORBIDDEN:
                if term in body and term not in allowed:
                    undeclared.append(f"{key}: `{term}`")
        self.assertEqual(
            undeclared,
            [],
            "엔진에 선언되지 않은 도메인 어휘가 있습니다. 어댑터로 옮기거나 "
            f"{DECLARED_LEAKS.name}에 이유와 함께 선언하세요:\n" + "\n".join(undeclared),
        )

    def test_declared_leaks_are_not_stale(self) -> None:
        """고쳐 놓고 선언만 남으면 목록이 거짓말이 된다."""
        stale: list[str] = []
        for file_key, terms in declared().items():
            path = OS_ROOT / file_key
            self.assertTrue(path.is_file(), f"선언된 파일이 없습니다: {file_key}")
            body = path.read_text(encoding="utf-8")
            stale.extend(f"{file_key}: `{term}`" for term in terms if term not in body)
        self.assertEqual(stale, [], "이미 사라진 누수가 선언에 남아 있습니다:\n" + "\n".join(stale))

    def test_engine_never_points_at_an_attribute_package(self) -> None:
        offenders = [
            str(path.relative_to(OS_ROOT))
            for path in engine_sources()
            if "attributes/" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(
            offenders, [], f"엔진이 속성 패키지 경로를 직접 가리킵니다: {offenders}"
        )


class EngineRunsWithoutTheAttributeTest(unittest.TestCase):
    """가방 패키지가 없다고 가정하고 사이클 후반부를 통째로 돌린다."""

    def build_attribute(self, root: Path) -> Path:
        policy_dir = root / "policy"
        (policy_dir / "precedents").mkdir(parents=True)
        (policy_dir / "policy.md").write_text(
            "---\nid: product-material\nversion: 1\nowner: tester\nupdatedAt: 2026-09-02\n---\n\n"
            "## 허용값\n\n- `COTTON` — 면이 대표 소재다\n- `UNKNOWN` — 혼용률을 못 읽는다\n\n"
            "## 근거 우선순위\n\n1. 라벨 택\n\n## 판정 불가 조건\n\n- 합이 100%가 아니다\n\n"
            "## 판례\n\n없음\n",
            encoding="utf-8",
        )
        run = root / "run"
        (run / "queue").mkdir(parents=True)
        (run / "reports").mkdir(parents=True)
        (run / "queue/ratio-gap.jsonl").write_text(
            json.dumps(
                {
                    "signal": "MATERIAL_RATIO_GAP",
                    "reason": "면 50%, 울 50%에서 대표 소재 기준이 없다.",
                    "productKey": "TEST:1",
                    "productName": "혼방 니트",
                    "referenceLabel": "COTTON",
                    "observedLabel": "UNKNOWN",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (run / "reports/policy-questions.json").write_text("[]", encoding="utf-8")
        (run / "manifest.json").write_text(
            json.dumps({"sourceDirty": False, "sourceCommit": "abc12345"}), encoding="utf-8"
        )
        profile = root / "profile.json"
        profile.write_text(
            json.dumps(
                {
                    "schemaVersion": "catalog-data-profile-v1",
                    "id": "product-material",
                    "displayName": "상품 소재 감사",
                    "attributeName": "대표 소재",
                    "subjectName": "의류 상품",
                    "outputRoot": str(run),
                    "labels": ["COTTON", "UNKNOWN"],
                    "policy": {
                        "owned": str(policy_dir / "policy.md"),
                        "precedents": str(policy_dir / "precedents"),
                    },
                    "signals": {
                        "MATERIAL_RATIO_GAP": {
                            "label": "혼용률 정책 공백",
                            "description": "대표 소재를 고를 기준이 없습니다.",
                            "priority": 1,
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return profile

    def test_pipeline_tail_runs_on_a_foreign_attribute(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = self.build_attribute(root)
            run = root / "run"
            for script in (
                "build_policy_index.py",
                "build_review_progress.py",
                "render_catalog_report.py",
            ):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(ENGINE_SCRIPTS / script),
                        "--profile",
                        str(profile),
                        "--output-root",
                        str(run),
                    ],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, f"{script} 실패:\n{result.stderr}")

            index = (run / "reports/catalog-audit.html").read_text(encoding="utf-8")
            self.assertIn("대표 소재", index)
            self.assertIn("혼용률 정책 공백", index)
            for name in ("gt-fixes.html", "policy-gaps.html"):
                report = (run / "reports" / name).read_text(encoding="utf-8")
                self.assertIn("혼방 니트", report, name)
                self.assertNotIn("MALE", report, name)
                self.assertNotIn("productGender", report, name)
            index = json.loads((run / "policy/policy-index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["owned"]["labels"], ["COTTON", "UNKNOWN"])




class GtTaskKeysAreReviewedTest(unittest.TestCase):
    """과제 프로필(`gtTask`)에 새 선언 키가 생기면, 그 키가 과제의 어휘를 담는지(어휘 수집에 넣을지) 누군가 정해야 한다.
    정하지 않은 새 키가 조용히 늘면 어휘 검사가 그 값을 못 본다 — 이번에 roleNames가 그랬다."""

    # 값이 과제의 어휘를 담을 수 있어 어휘 수집이 보는 키, 그리고 엔진의 구조·수치라 어휘가 아닌 키.
    REVIEWED_TASK = {"schemaVersion", "unit", "keyField", "groupField", "titleField", "titleAsEvidence", "linkField", "definitions",
                     "definitionsRoot", "gt", "authority", "correctionSourcePrefix", "fields", "constraints",
                     "images", "evidence", "columnNames", "prerequisite", "agents", "limit"}
    REVIEWED_IMAGES = {"alwaysKeepRoles", "path", "root", "keyField", "joinField", "listField", "fileField", "fileRoot", "fileBase", "idField",
                       "roleField", "roles", "roleNames", "matchField", "entryField", "tileRoles", "tileRule", "sourceIndexField",
                       "sourceListComplete", "preTiledRoles", "preTiledRule", "contextFields", "maxImages", "maxEdge",
                       "urlField", "urlSha256Field"}
    # 안쪽 블록도 같다 — 새 키의 값이 과제 어휘면 어휘 수집이 봐야 한다(rowCheck의 탭 이름처럼).
    REVIEWED_NESTED = {
        "gt": {"path", "root", "sourceField", "fieldSourcesField", "supersededBy", "upstream"},
        "upstream": {"kind", "note", "refresh", "acceptsEmpty", "columns", "headerNames", "locatorFields", "mirrorFields",
                     "rowCheck"},
        "field": {"id", "name", "labels", "labelNames", "valueCodes", "legacy", "gtField", "alternativesField", "fillMissing", "unknownLabel",
                  "cardinality", "valueType", "definition"},
        "constraint": {"id", "text", "when", "require", "forbid"},
        "evidence": {"textFields"},
        "authority": {"trusted", "reference", "default"},
        "promptDelivery": {"resource", "adapter", "field", "root", "beginMarker", "endMarker", "adapterTargets"},
        "adapterTarget": {"name", "pattern", "valueTemplate"},
        "policyCard": {"title", "description", "observations", "rules", "decisionRules"},
        "legacyResponseProjection": {"personPresenceField", "unknownGenderValue", "absentPersonValue", "unclearPersonValue", "legacyOnly"},
    }

    REVIEWED_POLICY_TASK = {"schemaVersion", "documentFormat", "definitions", "definitionsRoot", "fields", "policyCards",
                            "promptDelivery", "legacyResponseProjection"}

    def test_every_declared_key_has_been_reviewed(self) -> None:
        unknown = []
        for path in sorted((OS_ROOT / "attributes").glob("*/profile.json")):
            profile = json.loads(path.read_text(encoding="utf-8"))
            policy_task = profile.get("policyTask")
            if isinstance(policy_task, dict):
                unknown += [f"{path.parent.name}: policyTask.{key}" for key in policy_task if key not in self.REVIEWED_POLICY_TASK]
                policy_blocks = {
                    "field": policy_task.get("fields") or [],
                    "promptDelivery": [policy_task.get("promptDelivery") or {}],
                    "adapterTarget": (policy_task.get("promptDelivery") or {}).get("adapterTargets") or [],
                    "policyCard": policy_task.get("policyCards") or [],
                    "legacyResponseProjection": [policy_task.get("legacyResponseProjection") or {}],
                }
                for name, dicts in policy_blocks.items():
                    unknown += [f"{path.parent.name}: policyTask.{name}.{key}" for block in dicts for key in block
                                if key not in self.REVIEWED_NESTED[name]]
            task = profile.get("gtTask")
            if not isinstance(task, dict):
                continue
            unknown += [f"{path.parent.name}: gtTask.{key}" for key in task if key not in self.REVIEWED_TASK]
            unknown += [f"{path.parent.name}: images.{key}" for key in (task.get("images") or {}) if key not in self.REVIEWED_IMAGES]
            gt = task.get("gt") or {}
            blocks = {"gt": [gt], "upstream": [gt.get("upstream") or {}],
                      "field": task.get("fields") or [], "constraint": task.get("constraints") or [],
                      "evidence": [task.get("evidence") or {}], "authority": [task.get("authority") or {}],
                      "promptDelivery": [task.get("promptDelivery") or {}],
                      "adapterTarget": (task.get("promptDelivery") or {}).get("adapterTargets") or [],
                      "policyCard": task.get("policyCards") or []}
            for name, dicts in blocks.items():
                unknown += [f"{path.parent.name}: {name}.{key}" for block in dicts for key in block
                            if key not in self.REVIEWED_NESTED[name]]
        self.assertEqual(unknown, [], "새 선언 키 — 어휘 수집(GtHarnessKnowsNoTaskTest.vocabulary)에 넣을지 정하고 여기 적는다")


class PolicyIsTheOnlyValueListTest(unittest.TestCase):
    """GT 개선 과제의 허용값은 정책(정의 문서) 하나가 정한다. 프로필에 목록이 남으면 두 곳이 조용히 어긋난다 —
    감사 사이클이 없는 팩(정책 블록 없음)의 최상위 labels도 쓰는 곳 없이 남는 둘째 사본이다."""

    def test_task_only_profiles_carry_no_value_list(self) -> None:
        leaks = []
        for path in sorted((OS_ROOT / "attributes").glob("*/profile.json")):
            profile = json.loads(path.read_text(encoding="utf-8"))
            task = profile.get("gtTask")
            if not isinstance(task, dict):
                continue
            if "labels" in profile and not profile.get("policy"):
                leaks.append(f"{path.parent.name}: 최상위 labels")
            leaks += [f"{path.parent.name}: {field.get('id')}.{key}" for field in task.get("fields") or []
                      for key in ("labels", "labelNames") if key in field]
        self.assertEqual(leaks, [], "허용값은 정의 문서의 `### 허용값`에만 적는다")

    def test_every_real_task_pack_loads_its_values_from_its_policy(self) -> None:
        # 정책 문서의 모양이 틀리면 로더가 멈춘다 — 그 멈춤을 운영팀의 «GT 개선해줘»가 아니라 여기서 먼저 만난다.
        if str(ENGINE_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(ENGINE_SCRIPTS))
        import gt_task
        from catalog_profile import load_profile

        broken = []
        for path in sorted((OS_ROOT / "attributes").glob("*/profile.json")):
            profile = load_profile(path)
            if not isinstance(profile.get("gtTask"), dict):
                continue
            try:
                task = gt_task.load_task(profile)
            except gt_task.TaskError as error:
                broken.append(f"{path.parent.name}: {error}")
                continue
            broken += [f"{path.parent.name}: {field['id']} 값 없음" for field in task["fields"] if not field.get("labels")]
        self.assertEqual(broken, [])


class PackageTableTest(unittest.TestCase):
    """engine/package.md의 소유 표가 실제 폴더와 맞는가. 표에 없는 스크립트는 누가 왜 가졌는지 모르는 파일이다."""

    def test_every_script_and_contract_is_owned_in_the_table(self) -> None:
        table = (OS_ROOT / "engine/package.md").read_text(encoding="utf-8")
        files = [path.name for path in sorted((OS_ROOT / "engine/scripts").glob("*.py"))]
        files += [path.name for path in sorted((OS_ROOT / "engine/contracts").glob("*")) if path.is_file()]
        files += [path.name for path in sorted((OS_ROOT / "engine/workflows").glob("*.js"))]
        files += [path.name for path in sorted((OS_ROOT / "engine/agents").glob("*.md"))]
        files += [path.name for path in sorted((OS_ROOT / "engine/skills").iterdir()) if path.is_dir()]
        self.assertEqual([name for name in files if name not in table], [])


class GtHarnessKnowsNoTaskTest(unittest.TestCase):
    """GT 개선 하네스는 과제를 모른다. 새 과제의 어휘가 엔진에 스며들면 그 과제에 맞춰 엔진이 휜다.

    금지 어휘는 손으로 적지 않고 **지금 있는 모든 과제 프로필**에서 모은다 — 과제가 늘면 저절로 늘어난다.
    """

    HARNESS_DOCS = (
        "engine/skills/gt-improve/SKILL.md",
        "engine/agents/gt-blind-reader.md",
        "engine/agents/gt-defender.md",
        "engine/agents/policy-auditor.md",
        "engine/agents/case-normalizer.md",
        "engine/contracts/gt-task.md",
    )

    # 과제 라벨 가운데 엔진이 제 뜻으로 쓰는 낱말. 엔진의 어휘이지 과제의 어휘가 아니다.
    # productKey는 감사 사이클이 쓰는 엔진의 키 이름이라(판정 원장·서버의 /decide) 과제의 어휘가 아니다.
    # source·fieldSources·imageId·images는 엔진의 기본 열·블록 이름이다(과제가 선언하지 않으면 엔진이 쓰는 이름).
    GENERIC = {"UNKNOWN", "NONE", "TRUE", "FALSE", "true", "false", "id", "role", "file", "productKey",
               "source", "fieldSources", "imageId", "images",
               # «상품»은 과제 단위(unit)로도 쓰이지만 엔진이 제 뜻(판매 단위 일반)으로 쓰는 낱말이다.
               "상품",
               # «이름»·«상품 키»는 엔진이 제 뜻으로 쓰는 말이다(판정한 사람의 이름, 감사 사이클의 상품 키).
               # 상류 시트의 머리글(headerNames)이 같은 낱말을 쓰더라도 과제의 어휘가 아니다.
               "이름", "상품 키"}

    def vocabulary(self) -> set[str]:
        words: set[str] = set(FORBIDDEN)
        for path in sorted((OS_ROOT / "attributes").glob("*/profile.json")):
            profile = json.loads(path.read_text(encoding="utf-8"))
            task = profile.get("gtTask")
            policy = profile.get("policyTask") or task
            if not isinstance(policy, dict):
                continue
            words |= {str(profile.get(key)) for key in ("id", "displayName", "attributeName", "subjectName") if profile.get(key)}
            words |= {str(name) for name in (task or {}).get("evidence", {}).get("textFields", [])}
            images = (task or {}).get("images") or {}
            for key in ("roles", "tileRoles", "preTiledRoles", "contextFields"):
                words |= {str(item) for item in images.get(key) or []}
            # 주소·해시 열 이름은 그 팩의 색인이 정한 어휘다(가져오기 어댑터가 쓴다) — 엔진 문서가 알면 안 된다.
            words |= {str(images[key]) for key in ("urlField", "urlSha256Field") if images.get(key)}
            for key in ("keyField", "titleField", "groupField"):
                value = (task or {}).get(key)
                words |= {str(item) for item in (value if isinstance(value, list) else [value] if value else [])}
            # 허용값·이름표는 공통 policyTask가 가리키는 문서에서 읽는다.
            if str(ENGINE_SCRIPTS) not in sys.path:
                sys.path.insert(0, str(ENGINE_SCRIPTS))
            from gt_task import definition_values, resolve

            definitions_spec = policy
            definitions = resolve({**profile, "_path": str(path)},
                                  {"path": definitions_spec["definitions"], "root": definitions_spec.get("definitionsRoot") or "project"})
            self.assertTrue(definitions.is_file(), f"{path.parent.name}: 정의 문서가 없습니다 — {definitions}")
            for rows in definition_values(definitions).values():
                for code, name in rows:
                    words.add(code)
                    if name:
                        words.add(name)
            for field in policy.get("fields") or []:
                words.add(str(field["id"]))
                if field.get("name"):
                    words.add(str(field["name"]))
            for field in (task or {}).get("fields") or []:
                words |= {str(label) for label in field.get("labels") or []}
                words |= {str(name) for name in (field.get("labelNames") or {}).values()}
                words |= {str(old) for old in (field.get("legacy") or {})}
            words |= {str(item.get("id")) for item in (task or {}).get("constraints") or []}
            # 과제가 가리키는 파일의 열 이름·상류 시트의 열 이름·출처 규약도 과제의 어휘다.
            gt = (task or {}).get("gt") or {}
            upstream = gt.get("upstream") or {}
            words |= {str(gt[key]) for key in ("sourceField", "fieldSourcesField") if gt.get(key)}
            words |= {str(value) for value in (upstream.get("columns") or {}).values()}
            words |= {str(value) for value in (upstream.get("headerNames") or {}).values()}
            for grade in ("trusted", "reference"):
                words |= {re.sub(r"[\^$]", "", str(pattern)) for pattern in (task or {}).get("authority", {}).get(grade, [])}
            if (task or {}).get("correctionSourcePrefix"):
                words.add(str(task["correctionSourcePrefix"]))
            words |= {str(value) for value in ((task or {}).get("columnNames") or {}).values()}
            words |= {str(item) for key in ("mirrorFields", "locatorFields") for item in upstream.get(key) or []}
            words |= {str(value) for value in (upstream.get("rowCheck") or {}).values()}
            words |= {str(value) for value in (gt.get("supersededBy") or {}).values()}
            words |= {str(value) for key, value in images.items() if key.endswith("Field") and isinstance(value, str)}
            words |= {str(value) for value in (images.get("roleNames") or {}).values()}
            for field in (task or {}).get("fields") or []:
                words |= {str(field[key]) for key in ("gtField", "alternativesField") if field.get(key)}
        return {word for word in words if word not in self.GENERIC
                and (len(word) >= 3 or (len(word) >= 2 and not word.isascii()))}

    def test_harness_code_and_docs_carry_no_task_vocabulary(self) -> None:
        targets = [path for path in engine_sources() if path.name.startswith(("gt_", "gt-"))]
        # 하네스가 import하는 엔진 모듈도 하네스의 일부다(화면 머리·프로필 읽기). 거기 과제의 어휘가 있으면 하네스가 안다.
        imported = set()
        for path in list(targets):
            if path.suffix == ".py":
                imported |= set(re.findall(r"^\s*(?:from|import)\s+(\w+)", path.read_text(encoding="utf-8"), re.M))
        targets += [ENGINE_SCRIPTS / f"{name}.py" for name in sorted(imported)
                    if (ENGINE_SCRIPTS / f"{name}.py").is_file() and not name.startswith("gt_")]
        targets += [ENGINE_SCRIPTS / "serve_reports.py"]
        # common은 어느 패키지도 모르는 자리다. 과제의 어휘가 들어오면 그 약속이 깨진다.
        targets += sorted((OS_ROOT / "common").glob("*.py"))
        targets += [OS_ROOT / relative for relative in self.HARNESS_DOCS]
        leaks = []
        for path in targets:
            body = path.read_text(encoding="utf-8")
            for word in sorted(self.vocabulary()):
                # 영문 라벨은 낱말 경계로 본다 — «SIDE»가 «INSIDE» 안에서 걸리지 않게.
                pattern = rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])" if word.isascii() else re.escape(word)
                if re.search(pattern, body):
                    leaks.append(f"{path.relative_to(OS_ROOT)}: `{word}`")
        self.assertEqual(leaks, [], "GT 개선 하네스가 특정 과제의 어휘를 압니다:\n" + "\n".join(leaks))


if __name__ == "__main__":
    unittest.main()
