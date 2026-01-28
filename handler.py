import runpod
import requests
import base64
import time
import os
import glob

COMFY_HOST = "127.0.0.1:8188"
BASE_URI = f"http://{COMFY_HOST}"
OUTPUT_DIR = "/workspace/ComfyUI/output"


def wait_for_comfyui(timeout=300):
    """Aguarda ComfyUI ficar pronto."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{BASE_URI}/system_stats", timeout=5)
            if r.status_code == 200:
                return True
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(2)
    raise RuntimeError("ComfyUI não iniciou a tempo")


def queue_prompt(workflow):
    """Envia workflow para a fila do ComfyUI."""
    r = requests.post(f"{BASE_URI}/prompt", json={"prompt": workflow}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Erro ao enfileirar prompt: {r.status_code} - {r.text}")
    return r.json()["prompt_id"]


def poll_result(prompt_id, timeout=300):
    """Aguarda resultado do prompt."""
    start = time.time()
    while time.time() - start < timeout:
        r = requests.get(f"{BASE_URI}/history/{prompt_id}", timeout=10)
        if r.status_code == 200:
            data = r.json()
            if prompt_id in data:
                status = data[prompt_id]["status"]
                if status["status_str"] == "success" and status["completed"]:
                    return data[prompt_id]["outputs"]
                elif status["status_str"] == "error":
                    msgs = status.get("messages", [])
                    for key, val in msgs:
                        if key == "execution_error":
                            raise RuntimeError(
                                f"{val.get('node_type', 'Unknown')}: {val.get('exception_message', 'Unknown error')}"
                            )
                    raise RuntimeError(f"Execução falhou: {status}")
        time.sleep(0.5)
    raise RuntimeError(f"Timeout aguardando prompt {prompt_id}")


def collect_images(outputs):
    """Coleta imagens geradas como base64."""
    images = []
    for node_id, node_output in outputs.items():
        if "images" in node_output:
            for img_info in node_output["images"]:
                filename = img_info["filename"]
                subfolder = img_info.get("subfolder", "")
                img_type = img_info.get("type", "output")

                if img_type == "output":
                    path = os.path.join(OUTPUT_DIR, subfolder, filename)
                else:
                    path = os.path.join("/workspace/ComfyUI/temp", subfolder, filename)

                if os.path.exists(path):
                    with open(path, "rb") as f:
                        images.append({
                            "filename": filename,
                            "data": base64.b64encode(f.read()).decode("utf-8"),
                            "type": "base64"
                        })
                    os.remove(path)
    return images


def handler(event):
    """Handler principal do RunPod serverless."""
    try:
        job_input = event["input"]
        workflow = job_input.get("workflow")

        if not workflow:
            return {"error": "Campo 'workflow' é obrigatório"}

        prompt_id = queue_prompt(workflow)
        outputs = poll_result(prompt_id)
        images = collect_images(outputs)

        if not images:
            return {"error": "Nenhuma imagem gerada"}

        # Libera memória
        try:
            requests.post(
                f"{BASE_URI}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=30,
            )
        except Exception:
            pass

        return {"images": images}

    except Exception as e:
        return {"error": str(e), "refresh_worker": True}


# Aguarda ComfyUI e inicia worker
wait_for_comfyui()
runpod.serverless.start({"handler": handler})
