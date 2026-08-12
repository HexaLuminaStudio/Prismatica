from __future__ import annotations

from app.core.services.insight_prompts import (
    buildDependencyPrompt,
    summarizeDependencyData,
)
from app.view.widgets.freq_analyzer.dependency_engine import (
    DependencyParse,
    DepToken,
    HanLPDependencyParser,
    toConllU,
)


def _parserWithFakeClient():
    parser = HanLPDependencyParser.__new__(HanLPDependencyParser)
    parser._client = lambda _sentence, tasks: {
        "tok/fine": [["在", "学校"]],
        "pos/ctb": [["P", "NN"]],
        "dep": [[[2, "case"], [0, "root"]]],
    }
    parser._lastError = None
    parser._metadata = {
        "provider": "HanLP RESTful",
        "endpoint": parser.HANLP_API_URL,
        "language": parser.HANLP_LANGUAGE,
        "tasks": list(parser.HANLP_TASKS),
        "modelVersion": "test-model",
        "labelScheme": "backend-native",
    }
    return parser


def testHanlpCredentialAndEndpointAreHardcodedInDesktopParser():
    assert HanLPDependencyParser.HANLP_AUTH.startswith("MTA4")
    assert HanLPDependencyParser.HANLP_API_URL == "https://hanlp.hankcs.com/api"


def testHanlpRelationLabelsRemainLosslessThroughDirectAdapter():
    parser = _parserWithFakeClient()

    result = parser.parse("在学校")

    assert result.backend == "hanlp"
    assert [token.deprel for token in result.tokens] == ["case", "root"]
    assert result.provider == "HanLP RESTful"
    assert result.tasks == ["tok/fine", "pos/ctb", "dep"]
    assert parser.metadata()["modelVersion"] == "test-model"


def testRuleBackendPromptForbidsResearchInference():
    parse = DependencyParse(
        text="示例。",
        backend="rule",
        tokens=[DepToken(id=1, form="示例", head=0, deprel="ROOT")],
    )
    summary = summarizeDependencyData([parse])
    prompt = buildDependencyPrompt(summary, {"corpusName": "测试"})["user"]

    assert summary["backends"] == ["rule"]
    assert "教学演示" in prompt
    assert "不得" in prompt


def testBackendNativePosIsNotMisreportedAsUniversalPos():
    parse = DependencyParse(
        text="测试",
        backend="hanlp",
        tokens=[DepToken(id=1, form="测试", pos="NN", head=0, deprel="root")],
    )

    fields = toConllU(parse).splitlines()[-1].split("\t")

    assert fields[3] == "_"
    assert fields[4] == "NN"


def testHanlpReproducibilityMetadataIsIncludedInPromptAndExport():
    parse = _parserWithFakeClient().parse("在学校")

    summary = summarizeDependencyData([parse])
    prompt = buildDependencyPrompt(summary, {"corpusName": "测试"})["user"]
    conllu = toConllU(parse)

    assert "modelVersion=test-model" in prompt
    assert "labelScheme=backend-native" in prompt
    assert "# model_version = test-model" in conllu
    assert "# tasks = tok/fine,pos/ctb,dep" in conllu
