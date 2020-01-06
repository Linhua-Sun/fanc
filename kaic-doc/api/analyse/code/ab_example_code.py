# start snippet ab setup
import kaic
import kaic.plotting as klot
import matplotlib.pyplot as plt

hic_1mb = kaic.load("output/hic/binned/kaic_example_1mb.hic")
# end snippet ab setup


# start snippet ab matrix
ab = kaic.ABCompartmentMatrix.from_hic(hic_1mb)
# end snippet ab matrix

# start snippet ab subset
ab_chr18 = ab.matrix(('chr18', 'chr18'))
# end snippet ab subset

# start snippet ab klot-correlation
fig, ax = plt.subplots()
mp = klot.SquareMatrixPlot(ab, ax=ax,
                           norm='lin', colormap='RdBu_r',
                           vmin=-1, vmax=1,
                           draw_minor_ticks=False)
mp.plot('chr18')
plt.show()
# end snippet ab klot-correlation
fig.savefig('../kaic-doc/api/analyse/images/ab_1mb_correlation.png')


# start snippet ab ev
ev = ab.eigenvector()
# end snippet ab ev

# start snippet ab gc-ev
gc_ev = ab.eigenvector(genome='hg19_chr18_19.fa', force=True)
# end snippet ab gc-ev


# start snippet ab plot-ev
fig, ax = plt.subplots()
lp = klot.LinePlot(ab)
lp.plot('chr18')
plt.show()
# end snippet ab plot-ev
fig.savefig('../kaic-doc/api/analyse/images/ab_1mb_ev.png')

