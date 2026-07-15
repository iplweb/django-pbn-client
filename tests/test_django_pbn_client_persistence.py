from contextlib import contextmanager

import pytest
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, connection

import django_pbn_client.persistence as persistence_module
from django_pbn_client.persistence import (
    download_pbn_objects,
    get_or_download,
    get_total_count,
    sync_dictionary,
    upsert_pbn_object,
)


@pytest.fixture
def recording_atomic(monkeypatch):
    """Zastąp ``transaction.atomic`` no-opem rejestrującym wejście, żeby bez DB
    zweryfikować, że remote-fetch dzieje się PRZED otwarciem transakcji."""
    order = []

    @contextmanager
    def fake_atomic(*args, **kwargs):
        order.append("atomic-enter")
        yield

    monkeypatch.setattr(persistence_module.transaction, "atomic", fake_atomic)
    return order


def _element():
    return {
        "mongoId": "race-winner",
        "status": "ACTIVE",
        "verificationLevel": "VERIFIED",
        "verified": True,
        "versions": [{"current": True, "object": {}}],
    }


def test_download_uses_paginator_total_and_host_progress_wrapper():
    saved = []
    progress_calls = []

    class Elements:
        total_elements = 2

        def __iter__(self):
            yield {"mongoId": "one"}
            yield {"mongoId": "two"}

    def save(element, model_class, client=None):
        saved.append((element["mongoId"], model_class, client))

    def progress(elements, total, label):
        progress_calls.append((total, label))
        return elements

    model_class = object()
    client = object()
    download_pbn_objects(
        Elements(),
        model_class,
        label="Downloading",
        save=save,
        client=client,
        progress=progress,
    )

    assert progress_calls == [(2, "Downloading")]
    assert saved == [
        ("one", model_class, client),
        ("two", model_class, client),
    ]


def test_total_count_supports_sized_and_counted_iterables():
    class Counted:
        count = 7

    assert get_total_count([1, 2, 3]) == 3
    assert get_total_count(Counted()) == 7
    assert get_total_count(iter([1, 2])) is None


@pytest.mark.django_db
def test_insert_race_fallback_keeps_outer_transaction_usable():
    class RaceDoesNotExist(ObjectDoesNotExist):
        pass

    class WinningInstance:
        status = "OLD"
        verificationLevel = "OLD"
        verified = False
        versions = []
        save_calls = 0

        def save(self):
            self.save_calls += 1

    winner = WinningInstance()

    class MissingQuery:
        def get(self):
            raise RaceDoesNotExist

    class RaceManager:
        def select_for_update(self):
            return self

        def filter(self, **kwargs):
            assert kwargs == {"pk": "race-winner"}
            return MissingQuery()

        def create(self, **kwargs):
            raise IntegrityError("another worker inserted the row")

        def get(self, **kwargs):
            assert kwargs == {"pk": "race-winner"}
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            return winner

    class RaceModel:
        objects = RaceManager()
        DoesNotExist = RaceDoesNotExist

    result = upsert_pbn_object(_element(), RaceModel)

    assert result is winner
    assert winner.status == "ACTIVE"
    assert winner.verificationLevel == "VERIFIED"
    assert winner.verified is True
    assert winner.versions == [{"current": True, "object": {}}]
    assert winner.save_calls == 1


def test_get_or_download_returns_cached_without_fetch():
    sentinel = object()
    fetched = []

    class Manager:
        def get(self, pk):
            return sentinel

    class Model:
        objects = Manager()

        class DoesNotExist(Exception):
            pass

    def fetch(object_id):
        fetched.append(object_id)
        return {"mongoId": object_id}

    result = get_or_download(Model, "abc", fetch=fetch)

    assert result is sentinel
    assert fetched == []  # brak pobrania, gdy rekord jest w cache


def test_get_or_download_downloads_and_saves_when_absent():
    saved_instance = object()
    saved = []

    class NotFound(Exception):
        pass

    class Manager:
        def get(self, pk):
            raise NotFound

    class Model:
        objects = Manager()
        DoesNotExist = NotFound

    def fetch(object_id):
        return {"mongoId": object_id, "status": "ACTIVE"}

    def save(element, model_class, client=None):
        saved.append((element, model_class, client))
        return saved_instance

    result = get_or_download(Model, "xyz", fetch=fetch, save=save, client="C")

    assert result is saved_instance
    assert saved == [({"mongoId": "xyz", "status": "ACTIVE"}, Model, "C")]


def test_sync_dictionary_fetches_before_transaction_and_propagates_result(
    recording_atomic,
):
    order = recording_atomic

    def fetch():
        order.append("fetch")
        return [{"code": "1"}, {"code": "2"}]

    def upsert(payload):
        order.append(("upsert", payload))
        return "done"

    result = sync_dictionary(fetch, upsert)

    assert result == "done"
    # Remote fetch MUSI się wykonać zanim otworzymy transakcję.
    assert order == [
        "fetch",
        "atomic-enter",
        ("upsert", [{"code": "1"}, {"code": "2"}]),
    ]


def test_sync_dictionary_materializes_lazy_iterator_before_transaction(
    recording_atomic,
):
    order = recording_atomic

    def fetch():
        def gen():
            order.append("fetch-yield-1")
            yield 1
            order.append("fetch-yield-2")
            yield 2

        return gen()

    captured = {}

    def upsert(payload):
        captured["payload"] = payload

    sync_dictionary(fetch, upsert)

    # Generator w pełni skonsumowany PRZED transakcją (materializacja).
    assert order == ["fetch-yield-1", "fetch-yield-2", "atomic-enter"]
    assert captured["payload"] == [1, 2]
    assert isinstance(captured["payload"], list)


def test_sync_dictionary_passes_non_iterator_payload_through(recording_atomic):
    captured = {}

    # Dict jest Iterable, ale NIE Iterator — musi przejść bez zamiany na listę
    # kluczy.
    sync_dictionary(
        lambda: {"languages": [1, 2]},
        lambda payload: captured.setdefault("payload", payload),
    )

    assert captured["payload"] == {"languages": [1, 2]}
