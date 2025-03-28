from pathlib import Path
import argparse
import numpy as np

import torch
import torch.nn as nn

from adabmDCA.dataset import DatasetDCA
from adabmDCA.fasta import get_tokens
from adabmDCA.stats import get_freq_single_point, get_freq_two_points, get_correlation_two_points
from adabmDCA.utils import get_device, get_dtype
from adabmDCA.functional import one_hot

from arDCA import arDCA
from arDCA.parser import add_args_train


# import command-line input arguments
def create_parser():
    # Important arguments
    parser = argparse.ArgumentParser(description='Train a DCA model.')
    parser = add_args_train(parser)
    
    return parser

# def compute_mean_error(X1, X2):
#     """Compute the mean agreement between two predictions."""
#     return (X1.argmax(dim=-1) == X2.argmax(dim=-1)).float().mean(dim=0)

def main():
    
    # Load parser, training dataset and DCA model
    parser = create_parser()
    args = parser.parse_args()
    
    print("\n" + "".join(["*"] * 10) + f" Training arDCA model " + "".join(["*"] * 10) + "\n")
    # Set the device
    device = get_device(args.device)
    dtype = get_dtype(args.dtype)
    template = "{0:<30} {1:<50}"
    print(template.format("Input MSA:", str(args.data)))
    print(template.format("Output folder:", str(args.output)))
    print(template.format("Alphabet:", args.alphabet))
    print(template.format("Learning rate:", args.lr))
    print(template.format("L2 reg. for fields:", args.reg_h))
    print(template.format("L2 reg. for couplings:", args.reg_J))
    print(template.format("Entropic order:", "True" if not args.no_entropic_order else "False"))
    print(template.format("Convergence threshold:", args.epsconv))
    if args.pseudocount is not None:
        print(template.format("Pseudocount:", args.pseudocount))
    print(template.format("Random seed:", args.seed))
    print(template.format("Data type:", args.dtype))
    if args.path_graph is not None:
        print(template.format("Input graph:", str(args.path_graph)))
    if args.data_test is not None:
        print(template.format("Input test MSA:", str(args.data_test)))
    print("\n")
    
    # Check if the data file exist
    if not Path(args.data).exists():
        raise FileNotFoundError(f"Data file {args.data} not found.")
    
    # Create the folder where to save the model
    folder = Path(args.output)
    folder.mkdir(parents=True, exist_ok=True)
    
    if args.label is not None:
        file_paths = {
            "params" : folder / Path(f"{args.label}_params.pth"),
        }
        
    else:
        file_paths = {
            "params" : folder / Path(f"params.pth"),
        }
    
    # Import dataset
    print("Importing dataset...")
    dataset = DatasetDCA(
        path_data=args.data,
        path_weights=args.weights,
        alphabet=args.alphabet,
        clustering_th=args.clustering_seqid,
        no_reweighting=args.no_reweighting,
        device=device,
        dtype=dtype,
    )

    
    # Compute statistics of the data
    L = dataset.get_num_residues()
    q = dataset.get_num_states()
    
    # Import graph from file
    if args.path_graph:
        print("Importing graph...")
    graph = torch.load(args.path_graph) if args.path_graph else None

    model = arDCA(L=L, q=q, graph=graph).to(device=device) # model = arDCA(L=L, q=q).to(device=device, dtype=dtype)
    # tokens = get_tokens(args.alphabet)
    
    # Save the weights if not already provided
    if args.weights is None:
        if args.label is not None:
            path_weights = folder / f"{args.label}_weights.dat"
        else:
            path_weights = folder / "weights.dat"
        np.savetxt(path_weights, dataset.weights.cpu().numpy())
        print(f"Weights saved in {path_weights}")
        
    # Set the random seed
    torch.manual_seed(args.seed)
    
    if args.pseudocount is None:
        args.pseudocount = 1. / dataset.get_effective_size()
        print(f"Pseudocount automatically set to {args.pseudocount}.")
        
    data_oh = one_hot(dataset.data, num_classes=q).to(dtype)
    fi_target = get_freq_single_point(data=data_oh, weights=dataset.weights, pseudo_count=args.pseudocount)
    fij_target = get_freq_two_points(data=data_oh, weights=dataset.weights, pseudo_count=args.pseudocount)
    
    # Define the optimizer
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    
    print("\n")
    data_test_oh = None
    if args.data_test is not None:
        dataset_test = DatasetDCA(
            path_data=args.data_test,
            path_weights=None,
            alphabet=args.alphabet,
            clustering_th=None,
            no_reweighting=args.no_reweighting,
            device=device,
            dtype=dtype,
        )
        data_test_oh = one_hot(dataset_test.data, num_classes=q).to(dtype)
    
    
    loss = model.fit(
        X=data_oh,
        weights=dataset.weights,
        optimizer=optimizer,
        max_epochs=args.nepochs,
        epsconv=args.epsconv,
        pseudo_count=args.pseudocount,
        use_entropic_order=not args.no_entropic_order,
        fix_first_residue=False,
        reg_h=args.reg_h,
        reg_J=args.reg_J,
        X_test=data_test_oh,
    )

    # Compute Accuracy
    predictions = model.predict_third_ML(data_oh)
    err = model.compute_mean_error(predictions[:, 2 * model.L // 3:, :], data_oh[:, 2 * model.L // 3:, :]).mean().item()
    print(f"TRAIN SET: Mean agreement between the predicted and the true sequence: {err}\n")
    if args.data_test is not None:
        predictions_test = model.predict_third_ML(data_test_oh)
        err_test = model.compute_mean_error(predictions_test[:, 2 * model.L // 3:, :], data_test_oh[:, 2 * model.L // 3:, :]).mean().item()
        print(f"TEST SET: Mean agreement between the predicted and the true sequence: {err_test}\n")


    # Sample from the model
    samples = model.sample_autoregressive(data_oh[:, 2 * model.L // 3:, :])
    pi = get_freq_single_point(samples)
    pij = get_freq_two_points(samples)
    pearson, _ = get_correlation_two_points(fi=fi_target, fij=fij_target, pi=pi, pij=pij)
    print(f"Pearson correlation of the two-site statistics: {pearson:.3f}")
    print(f"Loss: {loss:.3f}\n")
    
    
    pearson_third, _ = get_correlation_two_points(fi=fi_target[2 * model.L // 3:, :], fij=fij_target[2 * model.L // 3:, :, 2 * model.L // 3:, :], pi=pi[2 * model.L // 3:, :], pij=pij[2 * model.L // 3:, :, 2 * model.L // 3:, :])
    print(f"Pearson correlation of the two-site statistics on the predicted sequence: {pearson_third:.3f}\n")
    
    # Save the model
    print("Saving the model...")
    torch.save(model.state_dict(), file_paths["params"])
    print(f"Model saved in {file_paths['params']}")

    
    
if __name__ == "__main__":
    main()