import pytest

import django_pbn_client
from django_pbn_client.download import DownloadResult, download_to_model
from django_pbn_client.pages import ThreadedModelSaver


class FakeResource:
    """Fake paginator: iterable sequentially, pageable for the threaded path."""

    total_pages = 2
    total_elements = 4

    def fetch_page(self, page_number):
        return [
            {"mongoId": f"page-{page_number}-element-{index}"} for index in range(2)
        ]

    def __iter__(self):
        for page_number in range(self.total_pages):
            yield from self.fetch_page(page_number)


def test_download_to_model_is_exported_from_package():
    assert django_pbn_client.download_to_model is download_to_model
    assert "download_to_model" in django_pbn_client.__all__


def test_factory_is_called_exactly_once_and_sequential_path_processes_all():
    factory_calls = []
    saved = []
    model_class = object()
    client = object()

    def factory():
        factory_calls.append(True)
        return FakeResource()

    def save(element, model, client=None):
        saved.append((element["mongoId"], model, client))

    result = download_to_model(factory, model_class, save=save, client=client)

    assert len(factory_calls) == 1
    assert sorted(item[0] for item in saved) == [
        "page-0-element-0",
        "page-0-element-1",
        "page-1-element-0",
        "page-1-element-1",
    ]
    assert all(item[1] is model_class for item in saved)
    assert all(item[2] is client for item in saved)
    assert result == DownloadResult(processed=4, errored=0)


@pytest.mark.parametrize("concurrency", [None, 1, "sequential"])
def test_sequential_concurrency_spellings_delegate_to_download_pbn_objects(
    concurrency, monkeypatch
):
    delegated = []

    def fake_download_pbn_objects(elements, model_class, *, save, client, progress):
        delegated.append((elements, model_class, save, client, progress))

    monkeypatch.setattr(
        "django_pbn_client.download.download_pbn_objects",
        fake_download_pbn_objects,
    )

    resource = FakeResource()
    model_class = object()

    result = download_to_model(lambda: resource, model_class, concurrency=concurrency)

    assert len(delegated) == 1
    assert delegated[0][0] is resource
    assert delegated[0][1] is model_class
    assert result == DownloadResult(processed=0, errored=0)


def test_sequential_progress_wrapper_receives_elements_total_and_label():
    progress_calls = []

    def progress(elements, total, label):
        progress_calls.append((total, label))
        return elements

    result = download_to_model(
        FakeResource,
        object(),
        save=lambda element, model, client=None: None,
        progress=progress,
    )

    assert len(progress_calls) == 1
    assert progress_calls[0][0] == 4
    assert result.processed == 4


def test_threaded_path_delegates_to_download_pages_with_bound_saver(monkeypatch):
    delegated = {}

    def fake_download_pages(client, data, *, getter_class, workers, progress):
        delegated.update(
            client=client,
            data=data,
            getter_class=getter_class,
            workers=workers,
            progress=progress,
        )

    monkeypatch.setattr(
        "django_pbn_client.download.download_pages",
        fake_download_pages,
    )

    resource = FakeResource()
    model_class = object()
    client = object()

    result = download_to_model(
        lambda: resource,
        model_class,
        concurrency=5,
        client=client,
    )

    assert delegated["client"] is client
    assert delegated["data"] is resource
    assert delegated["workers"] == 5
    assert issubclass(delegated["getter_class"], ThreadedModelSaver)
    assert delegated["getter_class"].model_class is model_class
    assert result == DownloadResult(processed=0, errored=0)


def test_threaded_path_processes_all_elements_end_to_end():
    saved = []
    model_class = object()
    client = object()

    def save(element, model, client=None):
        saved.append((element["mongoId"], model, client))

    result = download_to_model(
        FakeResource,
        model_class,
        save=save,
        concurrency=2,
        client=client,
    )

    assert sorted(item[0] for item in saved) == [
        "page-0-element-0",
        "page-0-element-1",
        "page-1-element-0",
        "page-1-element-1",
    ]
    assert all(item[1] is model_class for item in saved)
    assert all(item[2] is client for item in saved)
    assert result == DownloadResult(processed=4, errored=0)


def test_threads_spelling_uses_download_pages_default_workers(monkeypatch):
    delegated = {}

    def fake_download_pages(client, data, *, getter_class, workers=12, progress):
        delegated["workers"] = workers

    monkeypatch.setattr(
        "django_pbn_client.download.download_pages",
        fake_download_pages,
    )

    download_to_model(FakeResource, object(), concurrency="threads")

    assert delegated["workers"] == 12


def test_failing_elements_are_counted_as_errored_and_do_not_abort():
    saved = []

    def save(element, model, client=None):
        if element["mongoId"] == "page-0-element-1":
            raise RuntimeError("boom")
        saved.append(element["mongoId"])

    result = download_to_model(FakeResource, object(), save=save)

    assert result == DownloadResult(processed=3, errored=1)
    assert sorted(saved) == [
        "page-0-element-0",
        "page-1-element-0",
        "page-1-element-1",
    ]


@pytest.mark.parametrize("concurrency", [0, -3, "greenlets", 2.5])
def test_invalid_concurrency_is_rejected(concurrency):
    with pytest.raises(ValueError, match="concurrency"):
        download_to_model(FakeResource, object(), concurrency=concurrency)


def test_result_has_processed_and_errored_fields():
    result = DownloadResult(processed=2, errored=1)
    assert result.processed == 2
    assert result.errored == 1
