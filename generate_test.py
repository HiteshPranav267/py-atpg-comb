from pyatpg.trojan import TrojanDatasetGenerator, TrojanInserter, RandomStrategy
inserter = TrojanInserter(trigger="AND", payload="XOR", strategy=RandomStrategy())
generator = TrojanDatasetGenerator(inserter=inserter, output_dir="datasets")
generator.generate("benchmarks/c17.v", variants=3)
