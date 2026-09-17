"""All provider traffic is an in-memory HTTPX contract; no paid requests."""
import json
import os
import warnings
from uuid import uuid4

import httpx
import pytest

from app import ai_generate_cli as cli
from app import ai_generator as generator
from app.ai_generator import GenerationOptions, GeneratorError
from test_application_access import access_case
from test_ai_service import issue


@pytest.fixture
def inputs():
    credential = uuid4()
    return {"origin": "https://app.example.invalid", "material_id": uuid4(),
        "token": "reawote_ai_" + str(credential) + "." + "x" * 43,
        "api_key": "offline-provider-" + "y" * 24,
        "options": GenerationOptions(model="explicit-test-model", reason="Prepare a human-reviewed draft")}


def context(material_id):
    return {"context": {"material_id": str(material_id), "name": "Clay", "brand": {"id": str(uuid4()), "name": "Example"},
        "categories": [{"id": str(uuid4()), "value": "Ceramic"}], "collections": [],
        "source_urls": [{"id": str(uuid4()), "url": "https://example.invalid/material"}]},
        "context_hash": "a" * 64, "content_revision": 0}


def provider_response(**updates):
    return {"status": "completed", "model": "actual-test-model-2026-09-17", "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "role": "assistant", "status": "completed", "content": [
            {"type": "output_text", "text": json.dumps({"description": "A clay material.", "tags": ["Clay", "clay"]})}]}], **updates}


def make_packet(inputs, *, app_handler=None, provider_handler=None):
    return generator.generate(**inputs,
        app_transport=httpx.MockTransport(app_handler or (lambda _: httpx.Response(200, json=context(inputs["material_id"])))),
        provider_transport=httpx.MockTransport(provider_handler or (lambda _: httpx.Response(200, json=provider_response()))))


def test_provider_receives_only_minimal_text_without_ids_urls_reasons_or_service_credentials(inputs):
    requests = []
    def app(request):
        requests.append(request)
        assert bool(request.headers["authorization"] == "Bearer " + inputs["token"])
        return httpx.Response(200, json=context(inputs["material_id"]), headers={"set-cookie": "unexpected=ignored; Path=/"})
    def provider(request):
        requests.append(request)
        assert str(request.url) == generator.OPENAI_ENDPOINT
        assert bool(request.headers["authorization"] == "Bearer " + inputs["api_key"])
        data = json.loads(request.content)
        assert data["store"] is False and data["stream"] is False and data["background"] is False
        assert data["model"] == inputs["options"].model and data["max_output_tokens"] == 4096
        assert data["tools"] == [] and data["text"]["format"]["strict"] is True
        assert data["text"]["format"]["schema"]["additionalProperties"] is False
        assert json.loads(data["input"][0]["content"]) == {
            "name": "Clay", "brand": "Example", "categories": ["Ceramic"], "collections": []}
        assert all(key not in request.headers for key in ("cookie", "origin", "x-csrf-token"))
        assert bool(inputs["token"].encode() not in request.content and inputs["api_key"].encode() not in request.content)
        return httpx.Response(200, json=provider_response())
    packet = make_packet(inputs, app_handler=app, provider_handler=provider)
    assert len(requests) == 2
    assert packet.payload.model == "actual-test-model-2026-09-17"
    assert packet.requested_model == "explicit-test-model"
    assert packet.payload.tags == ["Clay"] and packet.payload.source_link_ids == []
    assert packet.payload.expected_context_hash == "a" * 64
    assert bool(inputs["token"] not in packet.model_dump_json() and inputs["api_key"] not in packet.model_dump_json())
    assert generator.load_packet(packet.model_dump_json()) == packet


@pytest.mark.parametrize("origin", ["http://app.example.invalid", "http://10.0.0.1", "http://127.0.0.1.evil.invalid",
    "https://example.invalid/path", "https://user@example.invalid", "https://example.invalid?", "https://example.invalid#",
    "https://example.invalid:0", "https://example.invalid:99999", "https://example.invalid\\x", "https://example.invalid\n", "file:///tmp/file"])
def test_unsafe_origins_rejected_before_network(origin, inputs):
    inputs["origin"] = origin
    def forbidden(_): pytest.fail("No request is allowed")
    with pytest.raises(GeneratorError, match="INVALID_APPLICATION_ORIGIN"):
        make_packet(inputs, app_handler=forbidden, provider_handler=forbidden)


@pytest.mark.parametrize("origin", ["http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000", "https://app.example.invalid"])
def test_explicit_https_or_loopback_origin_supported(origin):
    assert generator.origin_url(origin + "/") == origin


@pytest.mark.parametrize("change", ["private", "wrong-material", "wrong-digest", "wrong-source", "too-many", "wrong-type"])
def test_context_fails_closed_before_provider_calls(inputs, change):
    data = context(inputs["material_id"])
    if change == "private": data["context"]["folder_path"] = "private path"
    if change == "wrong-material": data["context"]["material_id"] = str(uuid4())
    if change == "wrong-digest": data["context_hash"] = "not a digest"
    if change == "wrong-source": data["context"]["source_urls"][0]["url"] = "http://127.0.0.1/private"
    if change == "too-many": data["context"]["categories"] *= 101
    if change == "wrong-type": data["content_revision"] = True
    def forbidden(_): pytest.fail("Invalid context must not be sent to the provider")
    with pytest.raises(GeneratorError):
        make_packet(inputs, app_handler=lambda _: httpx.Response(200, json=data), provider_handler=forbidden)


@pytest.mark.parametrize("change", ["refusal", "incomplete", "failed", "empty", "multiple", "tool", "wrong-role", "wrong-status",
    "html", "extra", "too-long", "tags", "empty-proposal", "invalid-model", "duplicate-key", "secret"])
def test_invalid_provider_result_never_becomes_a_packet(inputs, change):
    response = provider_response()
    message = response["output"][1]
    if change == "refusal": message["content"] = [{"type": "refusal", "refusal": "Do not print this upstream text"}]
    if change in {"incomplete", "failed"}: response["status"] = change
    if change == "empty": response["output"] = []
    if change == "multiple": response["output"].append(message)
    if change == "tool": response["output"].append({"type": "function_call"})
    if change == "wrong-role": message["role"] = "user"
    if change == "wrong-status": message["status"] = "in_progress"
    if change == "html": message["content"][0]["text"] = "<html>upstream error</html>"
    if change == "extra": message["content"][0]["text"] = json.dumps({"description": "Clay", "tags": [], "source_link_ids": [str(uuid4())]})
    if change == "too-long": message["content"][0]["text"] = json.dumps({"description": "x" * 5001, "tags": []})
    if change == "tags": message["content"][0]["text"] = json.dumps({"description": "Clay", "tags": ["one:two"]})
    if change == "empty-proposal": message["content"][0]["text"] = json.dumps({"description": "", "tags": []})
    if change == "invalid-model": response["model"] = "model\ncontrol"
    if change == "duplicate-key": message["content"][0]["text"] = '{"description":"a","description":"b","tags":[]}'
    if change == "secret": message["content"][0]["text"] = json.dumps({"description": inputs["api_key"], "tags": []})
    with pytest.raises(GeneratorError) as error:
        make_packet(inputs, provider_handler=lambda _: httpx.Response(200, json=response))
    assert bool(inputs["api_key"] not in str(error.value) and "upstream" not in str(error.value))


@pytest.mark.parametrize("mode", ["redirect", "rate-limit", "timeout", "oversize", "encoding", "content-type", "bad-json"])
def test_network_failures_are_bounded_sanitized_and_not_retried(inputs, mode):
    calls = []
    def provider(request):
        calls.append(request)
        if mode == "redirect": return httpx.Response(307, headers={"location": "https://elsewhere.invalid"})
        if mode == "rate-limit": return httpx.Response(429, text=inputs["api_key"])
        if mode == "timeout": raise httpx.ReadTimeout(inputs["api_key"])
        if mode == "oversize": return httpx.Response(200, content=b"x" * (generator.MAX_BYTES + 1), headers={"content-type": "application/json"})
        if mode == "encoding": return httpx.Response(200, content=b"", headers={"content-type": "application/json", "content-encoding": "gzip"})
        if mode == "content-type": return httpx.Response(200, text="private error")
        return httpx.Response(200, content=b"{", headers={"content-type": "application/json"})
    with pytest.raises(GeneratorError) as error:
        make_packet(inputs, provider_handler=provider)
    assert len(calls) == 1
    assert bool(inputs["api_key"] not in str(error.value) and "private" not in str(error.value))


def test_fresh_client_ignores_environment_proxy_and_never_redirects(monkeypatch, inputs):
    original = httpx.Client
    configurations = []
    def client(**kwargs):
        configurations.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setenv("HTTPS_PROXY", "http://unused.invalid:3128")
    make_packet(inputs)
    assert len(configurations) == 2
    assert all(item["trust_env"] is False and item["follow_redirects"] is False for item in configurations)


def test_submit_retries_the_same_packet_without_regeneration_and_validates_receipt(inputs):
    packet = make_packet(inputs)
    sends = []
    draft_id = uuid4()
    def receiver(request):
        sends.append(json.loads(request.content))
        if len(sends) == 1: raise httpx.ReadTimeout("Uncertain commit")
        return httpx.Response(201, json={"id": str(draft_id), "status": "AI_DRAFT", "context_hash": "a" * 64})
    arguments = {key: inputs[key] for key in ("origin", "material_id", "token")}
    arguments.update(packet=packet, transport=httpx.MockTransport(receiver))
    with pytest.raises(GeneratorError): generator.submit(**arguments)
    result = generator.submit(**arguments)
    assert result.id == draft_id and len(sends) == 2 and sends[0] == sends[1]
    with pytest.raises(GeneratorError, match="INVALID_RECEIPT"):
        generator.submit(**{**arguments, "transport": httpx.MockTransport(lambda _: httpx.Response(201,
            json={"id": str(draft_id), "status": "AI_DRAFT", "context_hash": "b" * 64}))})


@pytest.mark.parametrize("change", ["origin", "material", "credential"])
def test_packet_cannot_cross_origin_material_or_credential_namespace(inputs, change):
    packet = make_packet(inputs)
    arguments = {key: inputs[key] for key in ("origin", "material_id", "token")}
    if change == "origin": arguments["origin"] = "https://different.invalid"
    if change == "material": arguments["material_id"] = uuid4()
    if change == "credential": arguments["token"] = "reawote_ai_" + str(uuid4()) + "." + "x" * 43
    def forbidden(_): pytest.fail("Wrong scope must not be sent")
    with pytest.raises(GeneratorError, match="PACKET_SCOPE_MISMATCH"):
        generator.submit(**arguments, packet=packet, transport=httpx.MockTransport(forbidden))


@pytest.mark.parametrize("change", ["extra", "provider", "prompt", "source", "oversize"])
def test_packet_format_is_strict_and_cannot_claim_unread_sources(inputs, change):
    data = make_packet(inputs).model_dump(mode="json")
    if change == "extra": data["secret"] = "forbidden"
    if change == "provider": data["payload"]["provider"] = "Elsewhere"
    if change == "prompt": data["payload"]["prompt_version"] = "unverified"
    if change == "source": data["payload"]["source_link_ids"] = [str(uuid4())]
    raw = json.dumps(data) if change != "oversize" else " " * (generator.MAX_BYTES + 1)
    with pytest.raises(GeneratorError): generator.load_packet(raw)


def test_cli_generation_reserves_file_before_network_and_never_overwrites(monkeypatch, tmp_path, inputs, capsys):
    path = tmp_path / "proposal.json"
    packet = make_packet(inputs)
    calls = []
    answers = iter([inputs["token"], inputs["api_key"]])
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: next(answers))
    def generate(*args):
        calls.append(args)
        assert "pending or failed" in path.read_text()
        return packet
    monkeypatch.setattr(cli, "generate", generate)
    argv = ["generate", "--origin", inputs["origin"], "--material", str(inputs["material_id"]),
        "--packet", str(path), "--model", "explicit-test-model", "--reason", "Review"]
    assert cli.main(argv) == 0 and generator.load_packet(path.read_bytes()) == packet
    assert cli.main(argv) == 1 and generator.load_packet(path.read_bytes()) == packet and len(calls) == 1
    output = capsys.readouterr()
    assert bool(inputs["token"] not in output.out + output.err and inputs["api_key"] not in output.out + output.err)


def test_cli_failed_generation_leaves_non_submittable_marker_and_sanitized_error(monkeypatch, tmp_path, inputs, capsys):
    path = tmp_path / "failed.json"
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "unused")
    def fail(*_): raise GeneratorError("GENERATION_NOT_COMPLETED")
    monkeypatch.setattr(cli, "generate", fail)
    assert cli.main(["generate", "--origin", inputs["origin"], "--material", str(inputs["material_id"]),
        "--packet", str(path), "--model", "explicit-model", "--reason", "Review"]) == 1
    with pytest.raises(GeneratorError): cli.read_packet(path)
    assert "No automatic retry" in capsys.readouterr().err


def test_hidden_input_refuses_echo_fallback(monkeypatch):
    def insecure(_):
        warnings.warn("Cannot disable echo", cli.getpass.GetPassWarning)
        pytest.fail("Echo fallback must not be reached")
    monkeypatch.setattr(cli.getpass, "getpass", insecure)
    with pytest.raises(GeneratorError, match="HIDDEN_INPUT_UNAVAILABLE"):
        cli.hidden("Secret: ")


def test_invalid_command_arguments_do_not_echo_accidental_secrets(inputs, capsys):
    with pytest.raises(SystemExit): cli.main(["submit", "--api-key", inputs["api_key"]])
    assert bool(inputs["api_key"] not in capsys.readouterr().err)


def test_cli_submission_reuses_the_file_and_never_needs_a_provider_key(monkeypatch, tmp_path, inputs, capsys):
    packet = make_packet(inputs)
    path = tmp_path / "proposal.json"
    path.write_text(packet.model_dump_json(), encoding="utf-8")
    before = path.read_bytes()
    calls = []
    prompts = []
    def prompt(label):
        prompts.append(label)
        return inputs["token"]
    def send(*arguments):
        calls.append(arguments)
        if len(calls) == 1: raise GeneratorError("UPSTREAM_UNAVAILABLE")
        return generator.Receipt(id=uuid4(), status="AI_DRAFT", context_hash="a" * 64)
    monkeypatch.setattr(cli.getpass, "getpass", prompt)
    monkeypatch.setattr(cli, "submit", send)
    argv = ["submit", "--origin", inputs["origin"], "--material", str(inputs["material_id"]), "--packet", str(path)]
    assert cli.main(argv) == 1 and cli.main(argv) == 0
    assert calls[0] == calls[1] and path.read_bytes() == before
    assert len(prompts) == 2 and all("OpenAI" not in label for label in prompts)
    output = capsys.readouterr()
    assert "unchanged packet" in output.err and "AI_DRAFT received" in output.out
    assert bool(inputs["token"] not in output.out + output.err)


def test_non_regular_packet_files_are_rejected_without_blocking(tmp_path):
    if os.name == "posix":
        fifo = tmp_path / "not-a-file"
        os.mkfifo(fifo)
        with pytest.raises(GeneratorError, match="INVALID_PACKET_FILE"):
            cli.read_packet(fifo)
    with pytest.raises((OSError, GeneratorError)):
        cli.read_packet(tmp_path)


def test_slow_stream_hits_total_deadline(inputs, monkeypatch):
    # Two requests, each with start/first-chunk timestamps. Only provider is slow.
    ticks = iter([0, 0, 0, 121])
    monkeypatch.setattr(generator.time, "monotonic", lambda: next(ticks))
    with pytest.raises(GeneratorError, match="UPSTREAM_TIMEOUT"):
        make_packet(inputs)


def test_real_service_roundtrip_is_idempotent_and_never_adopts_generated_content(access_case, inputs):
    case = access_case
    material = case.materials[0]
    path = f"/api/materials/{material.id}"
    with case.client("ADMIN") as admin, case.client() as service:
        issued = issue(admin, path)
        def forward(request):
            forwarded_headers = {name: request.headers[name] for name in ("authorization", "content-type", "accept") if name in request.headers}
            result = service.request(request.method, request.url.raw_path.decode(),
                headers=forwarded_headers, content=request.content)
            return httpx.Response(result.status_code, content=result.content, headers={"content-type": "application/json"})
        transport = httpx.MockTransport(forward)
        inputs.update(material_id=material.id, token=issued["token"])
        packet = generator.generate(**inputs, app_transport=transport,
            provider_transport=httpx.MockTransport(lambda _: httpx.Response(200, json=provider_response())))
        assert admin.get(path + "/content-drafts").json()["items"] == []
        arguments = {key: inputs[key] for key in ("origin", "material_id", "token")}
        received = generator.submit(**arguments, packet=packet, transport=transport)
        assert generator.submit(**arguments, packet=packet, transport=transport) == received
        history = admin.get(path + "/content-drafts").json()["items"]
        assert len(history) == 1 and history[0]["id"] == str(received.id)
        assert history[0]["source_link_ids"] == [] and history[0]["service_credential_id"] == issued["credential"]["id"]
        assert admin.get(path + "/content").json()["revision"] == 0
        assert admin.post(path + "/ai-service-credentials/" + issued["credential"]["id"] + "/revoke",
            json={"idempotency_key": str(uuid4()), "reason": "Finished"}).status_code == 200
        with pytest.raises(GeneratorError, match="UPSTREAM_REJECTED"):
            generator.submit(**arguments, packet=packet, transport=transport)
