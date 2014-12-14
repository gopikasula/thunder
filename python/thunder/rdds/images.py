from numpy import ndarray, arange, amax, amin, size, squeeze

from thunder.rdds.data import Data
from thunder.rdds.keys import Dimensions


class Images(Data):
    """
    Distributed collection of images or volumes.

    Backed by an RDD of key-value pairs, where the key
    is an identifier and the value is a two or three-dimensional array.
    """

    _metadata = Data._metadata + ['_dims', '_nimages']

    def __init__(self, rdd, dims=None, nimages=None, dtype=None):
        super(Images, self).__init__(rdd, dtype=dtype)
        if dims and not isinstance(dims, Dimensions):
            try:
                dims = Dimensions.fromTuple(dims)
            except:
                raise TypeError("Images dims parameter must be castable to Dimensions object, got: %s" % str(dims))
        self._dims = dims
        self._nimages = nimages

    @property
    def dims(self):
        if self._dims is None:
            self.populateParamsFromFirstRecord()
        return self._dims

    @property
    def nimages(self):
        if self._nimages is None:
            self._nimages = self.rdd.count()
        return self._nimages

    @property
    def dtype(self):
        # override just calls superclass; here for explicitness
        return super(Images, self).dtype

    @property
    def _constructor(self):
        return Images

    def populateParamsFromFirstRecord(self):
        record = super(Images, self).populateParamsFromFirstRecord()
        self._dims = Dimensions.fromTuple(record[1].shape)
        return record

    def _resetCounts(self):
        self._nimages = None
        return self

    @staticmethod
    def _check_type(record):
        if not isinstance(record[0], tuple):
            raise Exception('Keys must be tuples')
        if not isinstance(record[1], ndarray):
            raise Exception('Values must be ndarrays')

    def toBlocks(self, blockSizeSpec="150M", padding=0):
        """Convert to Blocks, each representing a subdivision of the larger Images data.

        Parameters
        ----------
        blockSizeSpec: string memory size, tuple of integer splits per dimension, or instance of BlockingStrategy
            A string spec will be interpreted as a memory size string (e.g. "64M"). The resulting blocks will be
            generated by a SimpleBlockingStrategy to be close to the requested size.
            A tuple of positive ints will be interpreted as "splits per dimension", to be used to generate a
            SimpleBlockingStrategy.
            If an instance of BlockingStrategy is passed, it will be used to generate the returned Blocks.

        padding: nonnegative integer or tuple of int, optional, default 0
            If padding is >0, or a tuple of int all > 0, then blocks will be generated with `padding` voxels of
            additional padding on each dimension. These padding voxels will overlap with those in neighboring blocks,
            but will not be included when e.g. generating Series or Images data from the blocks. See
            thunder.rdds.imgblocks.strategy.PaddedBlockingStrategy for details.

        Returns
        -------
        Blocks instance
        """
        from thunder.rdds.imgblocks.strategy import BlockingStrategy, SimpleBlockingStrategy, PaddedBlockingStrategy
        stratClass = SimpleBlockingStrategy if not padding else PaddedBlockingStrategy
        if isinstance(blockSizeSpec, BlockingStrategy):
            blockingStrategy = blockSizeSpec
        elif isinstance(blockSizeSpec, basestring) or isinstance(blockSizeSpec, int):
            blockingStrategy = stratClass.generateFromBlockSize(self, blockSizeSpec, padding=padding)
        else:
            # assume it is a tuple of positive int specifying splits
            blockingStrategy = stratClass(blockSizeSpec, padding=padding)

        blockingStrategy.setImages(self)
        avgSize = blockingStrategy.calcAverageBlockSize()
        if avgSize >= BlockingStrategy.DEFAULT_MAX_BLOCK_SIZE:
            # TODO: use logging module here rather than print
            print "Thunder WARNING: average block size of %g bytes exceeds suggested max size of %g bytes" % \
                  (avgSize, BlockingStrategy.DEFAULT_MAX_BLOCK_SIZE)

        returntype = blockingStrategy.getBlocksClass()
        vals = self.rdd.flatMap(blockingStrategy.blockingFunction, preservesPartitioning=False)
        # fastest changing dimension (e.g. x) is first, so must sort reversed keys to get desired ordering
        # sort must come after group, b/c group will mess with ordering.
        groupedvals = vals.groupBy(lambda (k, _): k.spatialKey).sortBy(lambda (k, _): tuple(k[::-1]))
        # groupedvals is now rdd of (z, y, x spatial key, [(partitioning key, numpy array)...]
        blockedvals = groupedvals.map(blockingStrategy.combiningFunction)
        return returntype(blockedvals, dims=self.dims, nimages=self.nimages, dtype=self.dtype)

    def toSeries(self, blockSizeSpec="150M"):
        """Converts this Images object to a Series object.

        This method is equivalent to images.toBlocks(blockSizeSpec).toSeries().

        Parameters
        ----------
        blockSizeSpec: string memory size, tuple of integer splits per dimension, or instance of BlockingStrategy
            A string spec will be interpreted as a memory size string (e.g. "64M"). The resulting blocks will be
            generated by a SimpleBlockingStrategy to be close to the requested size.
            A tuple of positive ints will be interpreted as "splits per dimension", to be used to generate a
            SimpleBlockingStrategy.
            If an instance of BlockingStrategy is passed, it will be used to generate the returned Blocks.

        Returns
        -------
        new Series object
        """
        return self.toBlocks(blockSizeSpec).toSeries()

    def saveAsBinarySeries(self, outputdirname, blockSizeSpec="150M", overwrite=False):
        """Writes this Images object to disk as binary Series data.

       This method is equivalent to images.toBlocks(blockSizeSpec).saveAsBinarySeries(outputdirname, overwrite)

        Parameters
        ----------
        blockSizeSpec: string memory size, tuple of integer splits per dimension, or instance of BlockingStrategy
            A string spec will be interpreted as a memory size string (e.g. "64M"). The resulting blocks will be
            generated by a SimpleBlockingStrategy to be close to the requested size.
            A tuple of positive ints will be interpreted as "splits per dimension", to be used to generate a
            SimpleBlockingStrategy.
            If an instance of BlockingStrategy is passed, it will be used to generate the returned Blocks.

        outputdirname : string path or URI to directory to be created
            Output files will be written underneath outputdirname. This directory must not yet exist
            (unless overwrite is True), and must be no more than one level beneath an existing directory.
            It will be created as a result of this call.

        overwrite : bool
            If true, outputdirname and all its contents will be deleted and recreated as part
            of this call.

        Returns
        -------
        no return value
        """
        self.toBlocks(blockSizeSpec).saveAsBinarySeries(outputdirname, overwrite=overwrite)

    def exportAsPngs(self, outputdirname, fileprefix="export", overwrite=False,
                     collectToDriver=True):
        """Write out basic png files for two-dimensional image data.

        Files will be written into a newly-created directory on the local file system given by outputdirname.

        All workers must be able to see the output directory via an NFS share or similar.

        Parameters
        ----------
        outputdirname : string
            Path to output directory to be created. Exception will be thrown if this directory already
            exists, unless overwrite is True. Directory must be one level below an existing directory.

        fileprefix : string
            String to prepend to all filenames. Files will be named <fileprefix>00000.png, <fileprefix>00001.png, etc

        overwrite : bool
            If true, the directory given by outputdirname will first be deleted if it already exists.

        collectToDriver : bool, default True
            If true, images will be collect()'ed at the driver first before being written out, allowing
            for use of a local filesystem at the expense of network overhead. If false, images will be written
            in parallel by each executor, presumably to a distributed or networked filesystem.
        """
        dims = self.dims
        if not len(dims) == 2:
            raise ValueError("Only two-dimensional images can be exported as .png files; image is %d-dimensional." %
                             len(dims))

        from matplotlib.pyplot import imsave
        from io import BytesIO
        from thunder.rdds.fileio.writers import getParallelWriterForPath, getCollectedFileWriterForPath

        def toFilenameAndPngBuf(kv):
            key, img = kv
            fname = fileprefix+"%05d.png" % int(key)
            bytebuf = BytesIO()
            imsave(bytebuf, img, format="png")
            return fname, bytebuf.getvalue()

        bufrdd = self.rdd.map(toFilenameAndPngBuf)

        if collectToDriver:
            writer = getCollectedFileWriterForPath(outputdirname)(outputdirname, overwrite=overwrite)
            writer.writeCollectedFiles(bufrdd.collect())
        else:
            writer = getParallelWriterForPath(outputdirname)(outputdirname, overwrite=overwrite)
            bufrdd.foreach(writer.writerFcn)

    def maxProjection(self, axis=2):
        """
        Compute maximum projections of images / volumes
        along the specified dimension.

        Parameters
        ----------
        axis : int, optional, default = 2
            Which axis to compute projection along
        """
        if axis >= size(self.dims):
            raise Exception("Axis for projection (%s) exceeds image dimensions (%s-%s)" % (axis, 0, size(self.dims)-1))

        proj = self.rdd.mapValues(lambda x: amax(x, axis))
        # update dimensions to remove axis of projection
        newdims = list(self.dims)
        del newdims[axis]
        return self._constructor(proj, dims=newdims).__finalize__(self)

    def maxminProjection(self, axis=2):
        """
        Compute maximum-minimum projections of images / volumes
        along the specified dimension. This computes the sum
        of the maximum and minimum values along the given dimension.

        Parameters
        ----------
        axis : int, optional, default = 2
            Which axis to compute projection along
        """
        proj = self.rdd.mapValues(lambda x: amax(x, axis) + amin(x, axis))
        # update dimensions to remove axis of projection
        newdims = list(self.dims)
        del newdims[axis]
        return self._constructor(proj, dims=newdims).__finalize__(self)

    def subsample(self, samplefactor):
        """Downsample an image volume by an integer factor

        Parameters
        ----------
        samplefactor : positive int or tuple of positive ints
            Stride to use in subsampling. If a single int is passed, each dimension of the image
            will be downsampled by this same factor. If a tuple is passed, it must have the same
            dimensionality of the image. The strides given in a passed tuple will be applied to
            each image dimension.
        """
        dims = self.dims
        ndims = len(dims)
        if not hasattr(samplefactor, "__len__"):
            samplefactor = [samplefactor] * ndims
        samplefactor = [int(sf) for sf in samplefactor]

        if any((sf <= 0 for sf in samplefactor)):
            raise ValueError("All sampling factors must be positive; got " + str(samplefactor))

        sampleslices = [slice(0, dims[i], samplefactor[i]) for i in xrange(ndims)]
        newdims = [dims[i] / samplefactor[i] for i in xrange(ndims)]  # integer division

        return self._constructor(
            self.rdd.mapValues(lambda v: v[sampleslices]), dims=newdims).__finalize__(self)
            
    def gaussianFilter(self, sigma=2):
        """Spatially smooth images using a gaussian filter.

        This function will be applied to every image in the data set and can be applied
        to either images or volumes. In the latter case, filtering will be applied separately
        to each plane.

        parameters
        ----------
        sigma : int, optional, default=2
            Size of the filter neighbourhood in pixels
        """

        from scipy.ndimage.filters import gaussian_filter

        dims = self.dims
        ndims = len(dims)

        if ndims == 2:

            def filter(im):
                return gaussian_filter(im, sigma)

        if ndims == 3:

            def filter(im):
                im.setflags(write=True)
                for z in arange(0, dims[2]):
                    im[:, :, z] = gaussian_filter(im[:, :, z], sigma)
                return im

        return self._constructor(
            self.rdd.mapValues(lambda v: filter(v))).__finalize__(self)

    def medianFilter(self, size=2):
        """Spatially smooth images using a median filter.

        The filtering will be applied to every image in the collection and can be applied
        to either images or volumes. In the latter case, filtering will be applied separately
        to each plane.

        parameters
        ----------
        size: int, optional, default=2
            Size of the filter neighbourhood in pixels
        """

        from scipy.ndimage.filters import median_filter

        dims = self.dims
        ndims = len(dims)

        if ndims == 2:

            def filter(im):
                return median_filter(im, size)

        if ndims == 3:

            def filter(im):
                im.setflags(write=True)
                for z in arange(0, dims[2]):
                    im[:, :, z] = median_filter(im[:, :, z], size)
                return im

        return self._constructor(
            self.rdd.mapValues(lambda v: filter(v))).__finalize__(self)

    def planes(self, bottom, top, inclusive=True):
        """
        Subselect planes for three-dimensional image data.

        Parameters
        ----------
        bottom : int
            Bottom plane in desired selection

        top : int
            Top plane in desired selection

        inclusive : boolean, optional, default = True
            Whether returned subset of planes should include bounds
        """
        if len(self.dims) == 2 or self.dims[2] == 1:
            raise Exception("Cannot subselect planes, images must be 3D")

        if inclusive is True:
            zrange = arange(bottom, top+1)
        else:
            zrange = arange(bottom+1, top)

        if len(zrange) == 0:
            raise Exception("No planes selected with range (%g, %g) and inclusive=%s, "
                            "try a different range" % (bottom, top, inclusive))

        if zrange.min() < self.dims.min[2]:
            raise Exception("Cannot include plane %g, first plane is %g" % (zrange.min(), self.dims.min[2]))

        if zrange.max() > self.dims.max[2]:
            raise Exception("Cannout include plane %g, last plane is %g" % (zrange.max(), self.dims.max[2]))

        newdims = [self.dims[0], self.dims[1], size(zrange)]

        if size(zrange) < 2:
            newdims = newdims[0:2]

        return self._constructor(self.rdd.mapValues(lambda v: squeeze(v[:, :, zrange])),
                                 dims=newdims).__finalize__(self)

    def subtract(self, val):
        """
        Subtract a constant value or an image / volume from
        all images / volumes in the data set.

        Parameters
        ----------
        val : int, float, or ndarray
            Value to subtract
        """
        if size(val) != 1:
            if val.shape != self.dims:
                raise Exception('Cannot subtract image with dimensions %s '
                                'from images with dimension %s' % (str(val.shape), str(self.dims)))

        return self.apply(lambda x: x - val)

