from app.core.services.association_rule_service import mineAssociationRules


def testDirectionalRulesAreCorrectedAsTheDisplayedHypothesisFamily():
    transactions = (
        [["A", "B"]] * 8
        + [["A"]] * 2
        + [["B"]] * 2
        + [[]] * 8
    )

    result = mineAssociationRules(
        transactions,
        minSupport=0.01,
        minConfidence=0.1,
        minJointCount=1,
        familyWiseAlpha=1.0,
    )

    assert len(result) == 2
    assert result.attrs["testedPairCount"] == 1
    assert result.attrs["testedHypothesisCount"] == 2
    assert set(result["directional adjusted p-value"]) == set(result["adjusted p-value"])
    assert all(result["adjusted p-value"] >= result["raw p-value"])
