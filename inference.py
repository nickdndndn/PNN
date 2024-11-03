from pathlib import Path
from tqdm import tqdm

import torch
from torch.optim import SGD
from torch.nn import MSELoss
from torch.utils.data import DataLoader
from torchvision.transforms import Resize, RandomHorizontalFlip, RandomVerticalFlip, RandomRotation
from torchmetrics import MetricCollection, PeakSignalNoiseRatio, StructuralSimilarityIndexMeasure
from torchmetrics.image import SpectralAngleMapper, ErrorRelativeGlobalDimensionlessSynthesis, RelativeAverageSpectralError, SpectralDistortionIndex
from torchmetrics.regression import MeanSquaredError
from torchinfo import summary

from data_loader.DataLoader import DIV2K, GaoFen2, Sev2Mod, WV3, GaoFen2panformer
from PNN import PNNmodel
from utils import *
import matplotlib.pyplot as plt
import numpy as np
import time

SERVER = '/home/ubuntu/project'


def measure_gpu_throughput(model, input1_, input2_):
    input1 = input1_.to('cuda')
    input2 = input2_.to('cuda')
    model = model.to('cuda')

    ave_forward_throughput = []

    ave_start = time.time()
    for t in range(300):
        start = time.time()
        x = model(input1, input2)
        end = time.time()
        fwd_throughput = 1/(end-start)
        # print('forward_throughput is {:.4f}'.format(fwd_throughput))
        ave_forward_throughput.append(fwd_throughput)

    ave_fwd_throughput = np.mean(ave_forward_throughput[1:])

    print('Mean throughput over 300 runs: {:.4f}'.format(ave_fwd_throughput))


def measure_gpu_latency(model, input1_, input2_):
    input1 = input1_.to('cuda')
    input2 = input2_.to('cuda')
    model = model.to('cuda')

    repetitions = 300

    # GPU-WARM-UP
    for _ in range(20):  # Increase warm-up iterations to ensure GPU is fully warmed up
        _ = model(input1, input2)
        torch.cuda.synchronize()  # Ensure the warm-up operation completes

    # Measure performance
    timings = []
    with torch.no_grad():
        for _ in range(repetitions):
            start = time.time()
            _ = model(input1, input2)
            torch.cuda.synchronize()  # Ensure the operation completes
            end = time.time()
            latency = end - start
            timings.append(latency)

    mean_latency = np.mean(timings)
    print(
        f"Mean time over {repetitions} runs: {mean_latency} seconds")
    return mean_latency


def main():
    choose_dataset = 'WV3'  # choose dataset

    if choose_dataset == 'GaoFen2':
        dataset = eval('GaoFen2')
        tr_dir = 'data/pansharpenning_dataset/GF2/train/train_gf2.h5'
        eval_dir = 'data/pansharpenning_dataset/GF2/val/valid_gf2.h5'
        test_dir = 'data/pansharpenning_dataset/GF2/test/test_gf2_multiExm1.h5'
        checkpoint_dir = 'checkpoints/pnn_model_GF2/pnn_model_GF2_2023_07_17-11_30_23.pth.tar'
        ms_channel = 4
        ergas_l = 4
    elif choose_dataset == 'WV3':
        dataset = eval('WV3')
        tr_dir = 'data/pansharpenning_dataset/WV3/train/train_wv3.h5'
        eval_dir = 'data/pansharpenning_dataset/WV3/val/valid_wv3.h5'
        test_dir = 'data/pansharpenning_dataset/WV3/test/test_wv3_multiExm1.h5'
        checkpoint_dir = 'checkpoints/pnn_model_WV3/pnn_model_WV3_2023_07_24-19_19_31.pth.tar'
        ms_channel = 8
        ergas_l = 4
    else:
        print(choose_dataset, ' does not exist')

    # Prepare device
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print('Device: ', device)

    # Initialize DataLoader
    train_dataset = dataset(
        Path(tr_dir), transforms=[(RandomHorizontalFlip(1), 0.3), (RandomVerticalFlip(1), 0.3)])  # /home/ubuntu/project
    train_loader = DataLoader(
        dataset=train_dataset, batch_size=128, shuffle=True, drop_last=True)

    validation_dataset = dataset(
        Path(eval_dir))
    validation_loader = DataLoader(
        dataset=validation_dataset, batch_size=64, shuffle=True)

    test_dataset = dataset(
        Path(test_dir))
    test_loader = DataLoader(
        dataset=test_dataset, batch_size=1, shuffle=False)

    # Initialize Model, optimizer, criterion and metrics
    model = PNNmodel(scale=4, ms_channels=ms_channel, mslr_mean=train_dataset.mslr_mean.to(device), mslr_std=train_dataset.mslr_std.to(device), pan_mean=train_dataset.pan_mean.to(device),
                     pan_std=train_dataset.pan_std.to(device)).to(device)

    my_list = ['conv_3.weight', 'conv_3.bias']
    params = list(
        filter(lambda kv: kv[0] in my_list, model.parameters()))
    base_params = list(
        filter(lambda kv: kv[0] not in my_list, model.parameters()))

    optimizer = SGD([
        {'params': params},
        {'params': base_params, 'lr': 5e-9}
    ], lr=5e-8, momentum=0.9)

    criterion = MSELoss().to(device)

    metric_collection = MetricCollection({
        'psnr': PeakSignalNoiseRatio().to(device),
        'ssim': StructuralSimilarityIndexMeasure().to(device),
        'sam': SpectralAngleMapper().to(device),
        'ergas': ErrorRelativeGlobalDimensionlessSynthesis().to(device),
        'rase' : RelativeAverageSpectralError().to(device),
        'mse' : MeanSquaredError().to(device),
    })

    val_metric_collection = MetricCollection({
        'psnr': PeakSignalNoiseRatio().to(device),
        'ssim': StructuralSimilarityIndexMeasure().to(device),
        'sam': SpectralAngleMapper().to(device),
        'ergas': ErrorRelativeGlobalDimensionlessSynthesis().to(device),
        'rase' : RelativeAverageSpectralError().to(device),
        'mse' : MeanSquaredError().to(device),
    })

    test_metric_collection = MetricCollection({
        'psnr': PeakSignalNoiseRatio().to(device),
        'ssim': StructuralSimilarityIndexMeasure().to(device),
        'sam': SpectralAngleMapper().to(device),
        'ergas': ErrorRelativeGlobalDimensionlessSynthesis().to(device),
        'rase' : RelativeAverageSpectralError().to(device),
        'mse' : MeanSquaredError().to(device),
    })

    sdi_metric = SpectralDistortionIndex().to(device)



    tr_report_loss = 0
    val_report_loss = 0
    test_report_loss = 0
    tr_metrics = []
    val_metrics = []
    test_metrics = []
    sdi_results = []

    current_daytime = datetime.datetime.now().strftime("%Y_%m_%d-%H_%M_%S")
    steps = 200000
    save_interval = 1000
    report_interval = 50
    test_intervals = [100000, 200000, 300000, 400000,
                      500000, 600000, 700000, 800000, 900000, 1000000]
    evaluation_interval = [100000, 200000, 300000, 400000,
                           500000, 600000, 700000, 800000, 900000, 1000000]
    val_steps = 50
    continue_from_checkpoint = True

    # load checkpoint
    if continue_from_checkpoint:
        tr_metrics, val_metrics, test_metrics = load_checkpoint(torch.load(
            checkpoint_dir), model, optimizer, tr_metrics, val_metrics, test_metrics)
        print('Model Loaded ...')

    def scaleMinMax(x):
        return ((x - np.nanmin(x)) / (np.nanmax(x) - np.nanmin(x)))

    input_tensor1 = torch.randn(
        1, 1, 256, 256).to(device)  # Example input tensor 1
    input_tensor2 = torch.randn(
        1, ms_channel, 64, 64).to(device)  # Example input tensor 2
    model.eval()

    # measure_gpu_throughput(model, input_tensor1, input_tensor2)
    # measure_gpu_latency(model, input_tensor1, input_tensor2)

    idx = 15
    # evaluation mode
    model.eval()
    with torch.no_grad():
        test_iterator = iter(test_loader)
        for i, (pan, mslr, mshr) in enumerate(test_iterator):

            # forward
            pan, mslr, mshr = pan.to(device), mslr.to(
                device), mshr.to(device)
            mssr = model(pan, mslr)
            test_loss = criterion(mssr, mshr)
            test_metric = test_metric_collection.forward(mssr, mshr)
            test_report_loss += test_loss

            # Normalize preds and target for SDI
            # print(mssr.max())
            preds_normalized = mssr / mssr.max()
            target_normalized = mshr / mshr.max()

            # Calculate SDI on normalized predictions and targets
            sdi_value = sdi_metric(preds_normalized, target_normalized)
            # print(sdi_value)
            sdi_results.append(sdi_value.item())

            figure, axis = plt.subplots(nrows=1, ncols=4, figsize=(15, 5))
            axis[0].imshow((scaleMinMax(mslr.permute(0, 3, 2, 1).detach().cpu()[
                            0, ...].numpy())).astype(np.float32)[..., :3], cmap='viridis')
            axis[0].set_title('(a) LR')
            axis[0].axis("off")

            axis[1].imshow(pan.permute(0, 3, 2, 1).detach().cpu()[
                0, ...], cmap='gray')
            axis[1].set_title('(b) PAN')
            axis[1].axis("off")

            axis[2].imshow((scaleMinMax(mssr.permute(0, 3, 2, 1).detach().cpu()[
                            0, ...].numpy())).astype(np.float32)[..., :3], cmap='viridis')
            axis[2].set_title(
                f'(c) PNN {test_metric["psnr"]:.2f}dB/{test_metric["ssim"]:.4f}')
            axis[2].axis("off")

            axis[3].imshow((scaleMinMax(mshr.permute(0, 3, 2, 1).detach().cpu()[
                            0, ...].numpy())).astype(np.float32)[..., :3], cmap='viridis')
            axis[3].set_title('(d) GT')
            axis[3].axis("off")

            plt.savefig(f'results/Images_{choose_dataset}_{i}.png')

            mslr = mslr.permute(0, 3, 2, 1).detach().cpu().numpy()
            pan = pan.permute(0, 3, 2, 1).detach().cpu().numpy()
            mssr = mssr.permute(0, 3, 2, 1).detach().cpu().numpy()
            gt = mshr.permute(0, 3, 2, 1).detach().cpu().numpy()

            np.savez(f'results/img_array_{choose_dataset}_{i}.npz', mslr=mslr,
                     pan=pan, mssr=mssr, gt=gt)

        # compute metrics
        test_metric = test_metric_collection.compute()
        test_metric_collection.reset()

        # Compute the average SDI
        average_sdi = sum(sdi_results) / len(sdi_results)

        # Print final scores
        print(f"Final scores:\n"
              f"ERGAS: {test_metric['ergas'].item()}\n"
              f"SAM: {test_metric['sam'].item()}\n"
              f"PSNR: {test_metric['psnr'].item()}\n"
              f"SSIM: {test_metric['ssim'].item()}\n"
              f"RASE: {test_metric['rase'].item()}\n"
              f"MSE: {test_metric['mse'].item()}\n"
              f"D_lambda: {average_sdi:.4f}")


if __name__ == '__main__':
    main()
