import argparse
import json
import logging
import os
import os.path as osp
import time
import warnings
from collections import defaultdict, OrderedDict
from pathlib import Path

import numpy as np
from load_model import load_model, choose_method_arguments, add_method_arguments
from src.utils.metrics import estimate_pose, relative_pose_error, error_auc, symmetric_epipolar_distance_numpy, \
    epidist_prec
from src.utils.plotting import dynamic_alpha, error_colormap, make_matching_figure
from tqdm import tqdm


def load_vis_syn_pairs_npz(npz_root, npz_list, data_root, multi_model='infrared'):
    """Load information for scene and image pairs from npz files.
    Args:
        npz_root: Directory path for npz files
        npz_list: File containing the names of the npz files to be used
    """
    with open(npz_list, 'r') as f:
        npz_names = [name.split()[0] for name in f.readlines()]
    print(f"Parse {len(npz_names)} npz from {npz_list}.")

    total_pairs = 0
    scene_pairs = {}
    infrared_root = './data/megadepth/train/infrared/'
    depth_root = './data/megadepth/train/depth/'
    normal_root = './data/megadepth/train/normal/'
    sketch_root = './data/megadepth/train/sketch/'
    paint_root = './data/megadepth/train/paint/'
    event_root = "./data/megadepth/train/event/"
    if multi_model == 'infrared':
        muti_root = infrared_root
    elif multi_model == 'depth':
        muti_root = depth_root
    elif multi_model == 'normal':
        muti_root = normal_root
    elif multi_model == 'sketch':
        muti_root = sketch_root
    elif multi_model == 'paint':
        muti_root = paint_root
    elif multi_model == 'event':
        muti_root = event_root
    else:
        raise ValueError(f"Unknown model: {multi_model}")

    for name in npz_names:
        print(f"Loading {name}")

        scene_info = np.load(f"{npz_root}/{name}.npz", allow_pickle=True)
        pairs = []
        # Collect pairs
        for pair_info in scene_info['pair_infos']:
            total_pairs += 1
            (id0, id1), overlap_score, central_matches = pair_info
            im0 = scene_info['image_paths'][id0]
            im0_original = os.path.join(data_root, im0)
            im0_multi = os.path.join(muti_root, im0).replace('jpg', 'png')
            im1 = scene_info['image_paths'][id1]
            im1_original = os.path.join(data_root, im1)
            im1_multi = os.path.join(muti_root, im1).replace('jpg', 'png')
            K0 = scene_info['intrinsics'][id0].astype(np.float32)
            K1 = scene_info['intrinsics'][id1].astype(np.float32)

            # dist0 = np.array(scene_info['distortion_coefs'][id0][0], dtype=float)
            # dist1 = np.array(scene_info['distortion_coefs'][id1][1], dtype=float)
            dist0 = np.zeros(8, dtype=float)
            dist1 = np.zeros(8, dtype=float)

            # Compute relative pose
            T0 = scene_info['poses'][id0]
            T1 = scene_info['poses'][id1]
            T_0to1 = np.matmul(T1, np.linalg.inv(T0))
            T_1to0 = np.linalg.inv(T_0to1)
            pairs.append({'im0': im0_original, 'im1': im1_multi, 'dist0': dist0, 'dist1': dist1,
                          'K0': K0, 'K1': K1, 'T_0to1': T_0to1})
            pairs.append({'im0': im0_multi, 'im1': im1_original, 'dist0': dist0, 'dist1': dist1,
                          'K0': K0, 'K1': K1, 'T_0to1': T_0to1})

        scene_pairs[name] = pairs

    print(f"Loaded {total_pairs} pairs.")
    return scene_pairs


def save_matching_figure(path, img0, img1, mkpts0, mkpts1, inlier_mask, T_0to1, K0, K1, t_err=None, R_err=None,
                         name=None, conf_thr=5e-4):
    """ Make and save matching figures
    """
    Tx = np.cross(np.eye(3), T_0to1[:3, 3])
    E_mat = Tx @ T_0to1[:3, :3]
    mkpts0_inliers = mkpts0[inlier_mask]
    mkpts1_inliers = mkpts1[inlier_mask]
    color = None
    if inlier_mask is not None and len(inlier_mask) != 0:
        epi_errs = symmetric_epipolar_distance_numpy(mkpts0_inliers, mkpts1_inliers, E_mat, K0, K1)

        correct_mask = epi_errs < conf_thr
        precision = np.mean(correct_mask) if len(correct_mask) > 0 else 0
        n_correct = np.sum(correct_mask)

        # matching info
        alpha = dynamic_alpha(len(correct_mask))
        color = error_colormap(epi_errs, conf_thr, alpha=alpha)
        text_precision = [
            f'Precision({conf_thr:.2e}) ({100 * precision:.1f}%): {n_correct}/{len(mkpts0_inliers)}']
    else:
        text_precision = [
            f'No inliers after ransac']
        return

    if name is not None:
        text = [name]
    else:
        text = []

    if t_err is not None and R_err is not None:
        error_text = [f"err_t: {t_err:.2f} °", f"err_R: {R_err:.2f} °"]
        text += error_text

    text += text_precision

    # make the figure
    figure = make_matching_figure(img0, img1, mkpts0_inliers, mkpts1_inliers,
                                  color, text=text, path=path, dpi=150)


def save_matching_figure2(path, img0, img1, mkpts0, mkpts1, inlier_mask, T_0to1, K0, K1, t_err=None, R_err=None,
                          name=None, conf_thr=5e-4):
    """ Make and save matching figures
    """
    Tx = np.cross(np.eye(3), T_0to1[:3, 3])
    E_mat = Tx @ T_0to1[:3, :3]
    mkpts0_inliers = mkpts0
    mkpts1_inliers = mkpts1
    epi_errs = symmetric_epipolar_distance_numpy(mkpts0_inliers, mkpts1_inliers, E_mat, K0, K1)

    correct_mask = epi_errs < conf_thr
    precision = np.mean(correct_mask) if len(correct_mask) > 0 else 0
    n_correct = np.sum(correct_mask)

    # matching info
    alpha = dynamic_alpha(len(correct_mask))
    color = error_colormap(epi_errs, conf_thr, alpha=alpha)
    text_precision = [
        f'Precision({conf_thr:.2e}) ({100 * precision:.1f}%): {n_correct}/{len(mkpts0_inliers)}']

    if name is not None:
        text = [name]
    else:
        text = []

    if t_err is not None and R_err is not None:
        error_text = [f"err_t: {t_err:.2f} °", f"err_R: {R_err:.2f} °"]
        text += error_text

    text += text_precision

    # make the figure
    figure = make_matching_figure(img0, img1, mkpts0_inliers, mkpts1_inliers,
                                  color, text=text, path=path, dpi=150)


def aggregiate_scenes(scene_pose_auc, thresholds):
    """Averages the auc results for cloudy_cloud and cloudy_sunny scenes
    """
    temp_pose_auc = {}
    for npz_name in scene_pose_auc.keys():
        scene_name = 'synthetic'
        temp_pose_auc[scene_name] = [np.zeros(len(thresholds), dtype=np.float32), 0]  # [sum, total_number]
    for npz_name in scene_pose_auc.keys():
        scene_name = 'synthetic'
        temp_pose_auc[scene_name][0] += scene_pose_auc[npz_name]
        temp_pose_auc[scene_name][1] += 1

    agg_pose_auc = {}
    for scene_name in temp_pose_auc.keys():
        agg_pose_auc[scene_name] = temp_pose_auc[scene_name][0] / temp_pose_auc[scene_name][1]

    return agg_pose_auc


def calculate_epi_errs(mkpts0, mkpts1, inlier_mask, T_0to1, K0, K1):
    Tx = np.cross(np.eye(3), T_0to1[:3, 3])
    E_mat = Tx @ T_0to1[:3, :3]
    mkpts0_inliers = mkpts0[inlier_mask]
    mkpts1_inliers = mkpts1[inlier_mask]
    if inlier_mask is not None and len(inlier_mask) != 0:
        epi_errs = symmetric_epipolar_distance_numpy(mkpts0_inliers, mkpts1_inliers, E_mat, K0, K1)
    else:
        epi_errs = np.inf
    return epi_errs


def calculate_epi_errs_no_inlier(mkpts0, mkpts1, inlier_mask, T_0to1, K0, K1):
    Tx = np.cross(np.eye(3), T_0to1[:3, 3])
    E_mat = Tx @ T_0to1[:3, :3]
    mkpts0_inliers = mkpts0
    mkpts1_inliers = mkpts1
    epi_errs = symmetric_epipolar_distance_numpy(mkpts0_inliers, mkpts1_inliers, E_mat, K0, K1)
    return epi_errs


def eval_relapose(
        matcher,
        data_root,
        scene_pairs,
        ransac_thres,
        thresholds,
        save_figs,
        figures_dir=None,
        method=None,
        print_out=False,
        debug=False,
):
    scene_pose_auc = {}
    precs = {}
    precs_no_inlier = {}
    for scene_name in scene_pairs.keys():
        scene_dir = osp.join(figures_dir, scene_name.split(".")[0])
        if save_figs and not osp.exists(scene_dir):
            os.makedirs(scene_dir)

        pairs = scene_pairs[scene_name]
        statis = defaultdict(list)
        np.set_printoptions(precision=2)

        # Eval on pairs
        logging.info(f"\nStart evaluation on VisSyn \n")
        for i, pair in tqdm(enumerate(pairs), smoothing=.1, total=len(pairs)):
            if debug and i > 10:
                break

            T_0to1 = pair['T_0to1']
            im0 = pair['im0']
            im1 = pair['im1']

            match_res = matcher(im0, im1, pair['K0'], pair['K1'], pair['dist0'], pair['dist1'])

            matches = match_res['matches']
            new_K0 = match_res['new_K0']
            new_K1 = match_res['new_K1']
            mkpts0 = match_res['mkpts0']
            mkpts1 = match_res['mkpts1']

            # Calculate pose errors
            ret = estimate_pose(
                mkpts0, mkpts1, new_K0, new_K1, thresh=ransac_thres
            )

            if ret is None:
                R, t, inliers = None, None, None
                t_err, R_err = np.inf, np.inf
                epi_errs = np.array([]).astype(np.float32)
                epi_errs_no_inlier = np.array([]).astype(np.float32)
                statis['failed'].append(i)
                statis['R_errs'].append(R_err)
                statis['t_errs'].append(t_err)
                statis['epi_errs'].append(epi_errs)
                statis['epi_errs_no_inlier'].append(epi_errs_no_inlier)
                statis['inliers'].append(np.array([]).astype(np.bool_))
            else:
                R, t, inliers = ret
                t_err, R_err = relative_pose_error(T_0to1, R, t)
                epi_errs = calculate_epi_errs(mkpts0, mkpts1, inliers, T_0to1, new_K0, new_K1)
                epi_errs_no_inlier = calculate_epi_errs_no_inlier(mkpts0, mkpts1, inliers, T_0to1, new_K0, new_K1)

                statis['epi_errs'].append(epi_errs)
                statis['epi_errs_no_inlier'].append(epi_errs_no_inlier)
                statis['R_errs'].append(R_err)
                statis['t_errs'].append(t_err)
                statis['inliers'].append(inliers.sum() / len(mkpts0))
                if print_out:
                    logging.info(f"#M={len(matches)} R={R_err:.3f}, t={t_err:.3f}")

            multi_model = f'{args.multi_model}'

            if save_figs:
                img0_name = f"{f'{multi_model}' if multi_model in pair['im0'] else 'vis'}_{osp.basename(pair['im0']).split('.')[0]}"
                img1_name = f"{f'{multi_model}' if multi_model in pair['im1'] else 'vis'}_{osp.basename(pair['im1']).split('.')[0]}"
                fig_path = osp.join(scene_dir, f"{img0_name}_{img1_name}_after_ransac.jpg")
                save_matching_figure(path=fig_path,
                                     img0=match_res['img0_undistorted'] if 'img0_undistorted' in match_res.keys() else
                                     match_res['img0'],
                                     img1=match_res['img1_undistorted'] if 'img1_undistorted' in match_res.keys() else
                                     match_res['img1'],
                                     mkpts0=mkpts0,
                                     mkpts1=mkpts1,
                                     inlier_mask=inliers,
                                     T_0to1=T_0to1,
                                     K0=new_K0,
                                     K1=new_K1,
                                     t_err=t_err,
                                     R_err=R_err,
                                     name=method
                                     )
                fig_path = osp.join(scene_dir, f"{img0_name}_{img1_name}_before_ransac.jpg")
                save_matching_figure2(path=fig_path,
                                      img0=match_res['img0_undistorted'] if 'img0_undistorted' in match_res.keys() else
                                      match_res['img0'],
                                      img1=match_res['img1_undistorted'] if 'img1_undistorted' in match_res.keys() else
                                      match_res['img1'],
                                      mkpts0=mkpts0,
                                      mkpts1=mkpts1,
                                      inlier_mask=inliers,
                                      T_0to1=T_0to1,
                                      K0=new_K0,
                                      K1=new_K1,
                                      t_err=t_err,
                                      R_err=R_err,
                                      name=method
                                      )

        logging.info(f"Scene: {scene_name} Total samples: {len(pairs)} Failed:{len(statis['failed'])}. \n")
        pose_errors = np.max(np.stack([statis['R_errs'], statis['t_errs']]), axis=0)
        pose_auc = error_auc(pose_errors, thresholds)  # (auc@5, auc@10, auc@20)
        epi_err_thr = 5e-4

        dist_thresholds = [epi_err_thr]
        precs[scene_name] = epidist_prec(np.array(statis['epi_errs'], dtype=object), dist_thresholds,
                                         True, True)  # (prec@err_thr)
        precs_no_inlier[scene_name] = epidist_prec(np.array(statis['epi_errs_no_inlier'], dtype=object),
                                                   dist_thresholds, True, False)
        scene_pose_auc[scene_name] = 100 * np.array([pose_auc[f'auc@{t}'] for t in thresholds])
        logging.info(f"{scene_name} {pose_auc} {precs} {precs_no_inlier}")

    agg_pose_auc = aggregiate_scenes(scene_pose_auc, thresholds)

    agg_precs = {key: np.mean([precs[scene][key] for scene in precs.keys()]) for key in
                 precs[list(precs.keys())[0]].keys()}

    agg_precs_no_inlier = {key: np.mean([precs_no_inlier[scene][key] for scene in precs_no_inlier.keys()]) for key in
                           precs_no_inlier[list(precs_no_inlier.keys())[0]].keys()}

    return scene_pose_auc, agg_pose_auc, precs, precs_no_inlier, agg_precs, agg_precs_no_inlier


def test_relative_pose_vissyn(
        data_root_dir,
        method="xoftr",
        exp_name="VisSYN",
        ransac_thres=1.5,
        print_out=False,
        save_dir=None,
        save_figs=False,
        debug=False,
        args=None

):
    # save_dir = osp.join(save_dir, time)
    if method == "roma":
        if args.ckpt is None:
            save_ = "roma"
        else:
            save_ = args.ckpt.split("/")[-1].replace(".ckpt", "")
    else:
        save_ = args.ckpt.split("/")[-1].replace(".ckpt", "")
    path_ = osp.join(save_dir, method, save_)
    if args.debug:
        path_ = osp.join(save_dir, method, save_, "debug")
    if not osp.exists(path_):
        os.makedirs(path_)

    counter = 0
    if hasattr(args, 'thr'):
        path = osp.join(path_, f"{exp_name}_thresh_{args.thr}" + "_{}")
    else:
        path = osp.join(path_, f"{exp_name}" + "_{}")
    while osp.exists(path.format(counter)):
        counter += 1
    exp_dir = path.format(counter)
    os.mkdir(exp_dir)
    results_file = osp.join(exp_dir, "results.json")
    logging.basicConfig(
        filename=results_file.replace('.json', '.log'),
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    figures_dir = osp.join(exp_dir, "match_figures")
    if save_figs:
        os.mkdir(figures_dir)

    # Log args
    logging.info(f"args: {args}")

    # Init paths
    data_root_dir = './data/megadepth/test/'
    npz_root = "./third_party/LoFTR/assets/megadepth_test_1500_scene_info"
    npz_list = "./third_party/LoFTR/assets/megadepth_test_1500_scene_info/megadepth_test_1500.txt"
    data_root = data_root_dir

    # Load pairs
    scene_pairs = load_vis_syn_pairs_npz(npz_root, npz_list, data_root, args.multi_model)

    # Load method
    # matcher = eval(f"load_{method}")(args)
    matcher = load_model(method, args)
    thresholds = [5, 10, 20]
    # Eval
    scene_pose_auc, agg_pose_auc, precs, precs_no_inlier, agg_precs, agg_precs_no_inlier = eval_relapose(
        matcher,
        data_root,
        scene_pairs,
        ransac_thres=ransac_thres,
        thresholds=thresholds,
        save_figs=save_figs,
        figures_dir=figures_dir,
        method=method,
        print_out=print_out,
        debug=debug,
    )

    # Create result dict
    results = OrderedDict({"method": method,
                           "exp_name": exp_name,
                           "ransac_thres": ransac_thres,
                           "auc_thresholds": thresholds})
    results.update({key: value for key, value in vars(args).items() if key not in results})
    results.update({key: value.tolist() for key, value in agg_pose_auc.items()})
    results.update({key: value.tolist() for key, value in scene_pose_auc.items()})

    results.update({f"precs_{key}": value for key, value in precs.items()})

    results.update({f"precs_no_inlier_{key}": value for key, value in precs_no_inlier.items()})

    results.update({f"agg_precs_{key}": value for key, value in agg_precs.items()})

    results.update({f"agg_precs_no_inlier_{key}": value for key, value in agg_precs_no_inlier.items()})

    logging.info(f"Results: {json.dumps(results, indent=4)}")

    # Save to json file
    with open(results_file, 'w') as outfile:
        json.dump(results, outfile, indent=4)

    logging.info(f"Results saved to {results_file}")


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Benchmark Relative Pose')

    choose_method_arguments(parser)
    parser.add_argument('--exp_name', type=str, default="VisSYN")
    parser.add_argument('--data_root_dir', type=str, default="./data/METU_VisSYN/")
    parser.add_argument('--save_dir', type=str, default="./results_relative_megadepth_syn_pose/")
    parser.add_argument('--e_name', type=str, default=None)
    parser.add_argument('--ransac_thres', type=float, default=0.5)
    parser.add_argument('--print_out', action='store_true')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--save_figs', action='store_true')
    parser.add_argument('--multi_model', type=str, default='infrared',
                        choices=['infrared', 'depth', 'normal', 'sketch', 'paint', 'event'])

    args, remaining_args = parser.parse_known_args()

    add_method_arguments(parser, args.method)

    args = parser.parse_args()

    print(args)

    if args.e_name is not None:
        save_dir = osp.join(args.save_dir, args.e_name)
    else:
        save_dir = args.save_dir

    tt = time.time()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        test_relative_pose_vissyn(
            Path(args.data_root_dir),
            args.method,
            args.exp_name,
            ransac_thres=args.ransac_thres,
            print_out=args.print_out,
            save_dir=args.save_dir,
            save_figs=args.save_figs,
            debug=args.debug,
            args=args
        )
    print(f"Elapsed time: {time.time() - tt}")