# coding: utf-8
"""应用单实例守卫回归测试。"""
from __future__ import annotations

import uuid

from app.core.bootstrap.single_instance import SingleInstanceGuard


def testSingleInstanceRejectsSecondGuard() -> None:
    instanceName = f"Prismatica.Test.{uuid.uuid4()}"
    first = SingleInstanceGuard(instanceName)
    second = SingleInstanceGuard(instanceName)

    try:
        assert first.acquire() is True
        assert first.acquire() is True
        assert second.acquire() is False
    finally:
        second.release()
        first.release()


def testSingleInstanceCanBeReacquiredAfterRelease() -> None:
    instanceName = f"Prismatica.Test.{uuid.uuid4()}"
    first = SingleInstanceGuard(instanceName)
    second = SingleInstanceGuard(instanceName)

    assert first.acquire() is True
    first.release()
    try:
        assert second.acquire() is True
    finally:
        second.release()
