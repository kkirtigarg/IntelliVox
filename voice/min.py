import mlx_whisper

result = mlx_whisper.transcribe(
    "chunk.wav",
    path_or_hf_repo="mlx-community/whisper-turbo"
)