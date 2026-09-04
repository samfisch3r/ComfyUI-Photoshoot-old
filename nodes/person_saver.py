import json
import importlib
import os
import time


def _output_directory():
    try:
        folder_paths = importlib.import_module("folder_paths")
        return folder_paths.get_output_directory()
    except (ImportError, AttributeError):
        return os.path.abspath("output")


def _output_path(filename_prefix):
    prefix = (filename_prefix or "characters/person").strip()
    if not prefix:
        prefix = "characters/person"
    if not prefix.lower().endswith(".json"):
        prefix += ".json"

    output_dir = os.path.abspath(_output_directory())
    path = os.path.abspath(os.path.join(output_dir, prefix))
    if os.path.commonpath((output_dir, path)) != output_dir:
        raise ValueError("filename_prefix must stay inside the output directory")
    return path


class PhotoshootPersonSaver:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "filename_prefix": ("STRING", {"default": "characters/person"}),
            },
            "optional": {
                "override_json": ("STRING", {"default": "", "multiline": True}),
                "person_state": ("STRING", {"forceInput": True}),
            },
        }

    RETURN_TYPES = ()
    RETURN_NAMES = ()
    FUNCTION = "save_person"
    OUTPUT_NODE = True
    CATEGORY = "Photoshoot"
    DESCRIPTION = "Saves person state JSON to the ComfyUI output directory."

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return time.time()

    def save_person(self, filename_prefix="characters/person", override_json="",
                    person_state=""):
        data_to_save = override_json.strip() or person_state.strip()
        try:
            parsed_data = json.loads(data_to_save)
            path = _output_path(filename_prefix)
        except (json.JSONDecodeError, TypeError, ValueError, OSError) as error:
            print("[Photoshoot Person Saver] Error: %s" % error)
            return ()

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as output_file:
            json.dump(parsed_data, output_file, indent=4, ensure_ascii=False)

        print("[Photoshoot Person Saver] Saved person configuration to: %s" % path)
        return ()


NODE_CLASS_MAPPINGS = {"PhotoshootPersonSaver": PhotoshootPersonSaver}
NODE_DISPLAY_NAME_MAPPINGS = {"PhotoshootPersonSaver": "Save Photoshoot Person"}