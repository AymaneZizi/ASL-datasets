"""WMT-SLT: Data for the WMT shared task on sign language translation"""
import json
import os
from os import path

import requests
import tensorflow_datasets as tfds
import tensorflow as tf
from tensorflow.python.platform.gfile import GFile

from sign_language_datasets.utils.features import PoseFeature
from .utils import get_framerate, read_mediapipe_surrey_format, read_openpose_surrey_format, reduce_pose_people

from ...datasets import SignDatasetConfig
import srt

_DESCRIPTION = """
These are Standard German daily news (Tagesschau) and Swiss German weather forecast (Meteo) episodes broadcast and interpreted into Swiss German Sign Language by hearing interpreters (among them, children of Deaf adults, CODA) via Swiss National TV (Schweizerisches Radio und Fernsehen, SRF) (https://www.srf.ch/play/tv/sendung/tagesschau-in-gebaerdensprache?id=c40bed81-b150-0001-2b5a-1e90e100c1c0). For a more extended description of the data, visit https://www.wmt-slt.com/data.
"""

_CITATION = """
@misc{muller_mathias_2022_6637392,
  author       = {Müller Mathias and
                  Ebling Sarah and
                  Camgöz Necati Cihan and
                  Jian Zifan and
                  Battisti Alessia and
                  Tissi Katja and
                  Sidler-Miserez Sandra and
                  Perrollaz Regula and
                  Berger Michèle and
                  Reinhard Sabine and
                  Moryossef Amit and
                  Rios Annette and
                  Bowden Richard and
                  Wong Ryan and
                  Ribback Robin and
                  Schori Severine},
  title        = {{WMT-SLT SRF: Training data for the WMT shared task 
                   on sign language translation (videos, subtitles)}},
  month        = jun,
  year         = 2022,
  note         = {{We additionally acknowledge funding through the 
                   Innosuisse Flagship "Inclusive Information and
                   Communication Technologies" (IICT) (grant
                   agreement no. PFFS-21-47).}},
  publisher    = {Zenodo},
  version      = {V1.2},
  doi          = {10.5281/zenodo.6637392},
  url          = {https://doi.org/10.5281/zenodo.6637392}
}
"""

_POSE_HEADERS = {
    "holistic": path.join(path.dirname(path.realpath(__file__)), "holistic.poseheader"),
    "openpose": path.join(path.dirname(path.realpath(__file__)), "openpose_135.poseheader"),
}

# these IDs change if a new version is released on Zenodo
ZENODO_DEPOSIT_ID_SRF_POSES = 6631275
ZENODO_DEPOSIT_ID_SRF_VIDEOS_SUBTITLES = 6637392
ZENODO_DEPOSIT_ID_FOCUS_NEWS = 6631159


class WMTSLT(tfds.core.GeneratorBasedBuilder):
    """DatasetBuilder for wmt slt srf dataset."""

    VERSION = tfds.core.Version('1.2.0')
    RELEASE_NOTES = {
        '1.2.0': '10.5281/zenodo.6637392 Jun 12, 2022',
    }

    BUILDER_CONFIGS = [
        SignDatasetConfig(name="default", process_video=True),
        SignDatasetConfig(name="annotations", process_video=False),
    ]

    def __init__(self,
                 zenodo_srf_poses_token: str,
                 zenodo_srf_videos_token: str,
                 zenodo_focusnews_token: str,
                 **kwargs):
        super().__init__(**kwargs)

        self.zenodo_srf_poses_token = zenodo_srf_poses_token
        self.zenodo_srf_videos_token = zenodo_srf_videos_token
        self.zenodo_focusnews_token = zenodo_focusnews_token

    def _info(self) -> tfds.core.DatasetInfo:
        """Returns the dataset metadata."""

        features = {
            "id": tfds.features.Text(),
            "source": tfds.features.Text(),  # srf|focusnews
            "fps": tf.int32,
            "subtitles": tfds.features.Sequence({
                "start": tfds.features.Text(),
                "end": tfds.features.Text(),
                "text": tfds.features.Text(),
            }),
        }

        # Add poses if requested
        if self._builder_config.include_pose is not None:
            pose_header_path = _POSE_HEADERS[self._builder_config.include_pose]

            if self._builder_config.include_pose == "openpose":
                pose_shape = (None, 1, 135, 2)
            elif self._builder_config.include_pose == "holistic":
                pose_shape = (None, 1, 203, 3)
            else:
                raise Exception("Unknown pose format")

            features["pose"] = PoseFeature(shape=pose_shape, dtype=tf.float16)

        if self._builder_config.process_video:
            features["video"] = self._builder_config.video_feature((720, 640))
        else:
            features["video"] = tfds.features.Text()

        return tfds.core.DatasetInfo(
            builder=self,
            description=_DESCRIPTION,
            features=tfds.features.FeaturesDict(features),
            homepage="https://www.wmt-slt.com/data",
            supervised_keys=None,
            citation=_CITATION,
        )

    def download_zenodo_deposit(self, dl_manager: tfds.download.DownloadManager,
                                zenodo_deposit_id: int,
                                zenodo_token: str):
        # using private link to set cookies
        record_link = f"https://zenodo.org/record/{zenodo_deposit_id}?token={zenodo_token}"
        record_request = requests.get(record_link)
        cookie = record_request.headers['Set-Cookie'].split(';')[0]

        # query the API for information about the deposit, extract the data link from the response
        record_api_link = f"https://zenodo.org/api/records/{zenodo_deposit_id}"
        headers = {'Cookie': cookie}
        record_api_response = json.loads(requests.get(record_api_link, headers=headers).text)

        zip_file = record_api_response["files"][0]["links"]["self"]
        archive = dl_manager.download_and_extract(tfds.download.Resource(url=zip_file, headers=headers))
        return archive

    def _split_generators(self, dl_manager: tfds.download.DownloadManager):
        srf_poses = self.download_zenodo_deposit(dl_manager, ZENODO_DEPOSIT_ID_SRF_POSES, self.zenodo_srf_poses_token)
        srf_poses_dir = path.join(srf_poses, 'srf', 'parallel')

        srf_videos = self.download_zenodo_deposit(dl_manager, ZENODO_DEPOSIT_ID_SRF_VIDEOS_SUBTITLES,
                                                  self.zenodo_srf_videos_token)
        srf_parallel_dir = path.join(srf_videos, 'srf', 'parallel')

        focusnews = self.download_zenodo_deposit(dl_manager, ZENODO_DEPOSIT_ID_FOCUS_NEWS, self.zenodo_focusnews_token)
        focusnews_dir = path.join(focusnews, "focusnews")

        datasets = {
            'srf': {
                'videos': path.join(srf_parallel_dir, 'videos'),  # "srf.2020-03-13.mp4"
                'subtitles': path.join(srf_parallel_dir, 'subtitles'),  # "srf.2020-03-13.srt"
                'openpose': path.join(srf_poses_dir, 'openpose'),  # "srf.2020-03-13.openpose.tar.xz"
                'mediapipe': path.join(srf_poses_dir, 'mediapipe'),  # "srf.2020-03-13.mediapipe.tar.xz"
            },
            'focusnews': {
                'videos': path.join(focusnews_dir, 'videos'),  # "focusnews.043.mp4"
                'subtitles': path.join(focusnews_dir, 'subtitles'),  # "focusnews.043.srt"
                'openpose': path.join(focusnews_dir, 'openpose'),  # "focusnews.043.openpose.tar.xz"
                'mediapipe': path.join(focusnews_dir, 'mediapipe'),  # "focusnews.043.mediapipe.tar.xz"
            }
        }

        print(f"SRF data stored in {srf_parallel_dir} and {srf_poses_dir}")
        print(f"FocusNews data stored in {focusnews}")

        return {
            'train': self._generate_examples(datasets)
        }

    def _generate_examples(self, datasets):
        """Yields examples."""

        for dataset_id, directories in datasets.items():
            names = [n[:-len('.mp4')] for n in os.listdir(directories['videos'])]
            for name in names:
                video_path = path.join(directories['videos'], f'{name}.mp4')
                subtitles_path = path.join(directories['subtitles'], f'{name}.srt')
                openpose_path = path.join(directories['openpose'], f'{name}.openpose.tar.xz')
                mediapipe_path = path.join(directories['mediapipe'], f'{name}.mediapipe.tar.xz')

                datum = {
                    "id": name,
                    "source": dataset_id,
                    "fps": get_framerate(video_path),
                    "video": video_path
                }

                with GFile(subtitles_path, "r") as f:
                    subtitles = srt.parse(f.read())
                    datum["subtitles"] = [{
                        "start": str(s.start),
                        "end": str(s.end),
                        "text": s.content
                    } for s in subtitles]

                if self.builder_config.include_pose is not None:
                    if self.builder_config.include_pose == "openpose":
                        datum["pose"] = read_openpose_surrey_format(openpose_path, datum["fps"])

                    if self.builder_config.include_pose == "holistic":
                        datum["pose"] = read_mediapipe_surrey_format(mediapipe_path, datum["fps"])
                        with open('holistic.poseheader', 'wb') as f:
                            datum["pose"].header.write(f)

                    reduce_pose_people(datum["pose"])

                yield datum["id"], datum
